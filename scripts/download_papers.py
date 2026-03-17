from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import time
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote

import arxiv
import requests

from init_pipeline import build_paths


USER_AGENT = "DFTauto-literature-review/0.2 (open-access downloader)"
FAST_TIMEOUT = 15
DEEP_TIMEOUT = 30
TITLE_MATCH_THRESHOLD = 0.84

LOGGER = logging.getLogger("download_papers")


def slugify(value: str, limit: int = 120) -> str:
    value = value.strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    return value[:limit].strip("_") or "paper"


def normalize_title(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def title_similarity(left: str, right: str) -> float:
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def read_queue(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_log(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "title",
        "doi",
        "priority",
        "score_total",
        "status",
        "source",
        "target_round",
        "timestamp",
        "message",
        "saved_path",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def source_counts(rows: list[dict]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("status") not in {"downloaded", "already_downloaded"}:
            continue
        source = row.get("source", "") or "local"
        counts[source] += 1
    return counts


def write_summary_markdown(path: Path, round_label: str, rows: list[dict]) -> None:
    downloaded = [row for row in rows if row["status"] in {"downloaded", "already_downloaded"}]
    failed = [row for row in rows if row["status"] == "manual_download_needed"]
    counts = source_counts(rows)
    ordered_sources = ["arXiv", "Semantic Scholar", "Unpaywall", "Crossref", "CORE", "PMC", "OpenAlex", "chemRxiv", "local"]
    lines = [
        f"# {round_label} 下载记录",
        "",
        f"- 尝试数量: {len(rows)}",
        f"- 成功或已存在: {len(downloaded)}",
        f"- 下载失败/需跳过: {len(failed)}",
        "",
        "## 成功或已存在",
        "",
    ]
    if downloaded:
        for row in downloaded:
            lines.append(
                f"- {row['title']} | 状态: {row['status']} | 来源: {row.get('source', '') or 'local'} | 路径: {row.get('saved_path', '')}"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "## 下载失败或跳过", ""])
    if failed:
        for row in failed:
            lines.append(
                f"- {row['title']} | 状态: {row['status']} | 原因: {row.get('message', '') or '未返回原因'}"
            )
    else:
        lines.append("- 无")

    lines.extend(
        [
            "",
            "## 渠道命中统计",
            "",
            "| 渠道 | 成功下载 |",
            "|------|---------|",
        ]
    )
    for source in ordered_sources:
        lines.append(f"| {source} | {counts.get(source, 0)} |")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary_json(path: Path, round_label: str, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "round": round_label,
                "attempted": len(rows),
                "downloaded_or_existing": [row for row in rows if row["status"] in {"downloaded", "already_downloaded"}],
                "failed_or_skipped": [row for row in rows if row["status"] == "manual_download_needed"],
                "source_counts": dict(source_counts(rows)),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    stream: bool = False,
    allow_redirects: bool = True,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = session.request(
                method,
                url,
                timeout=timeout,
                headers=headers,
                params=params,
                stream=stream,
                allow_redirects=allow_redirects,
            )
            if response.status_code == 429 and attempt == 0:
                time.sleep(2)
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 429 and attempt == 0:
                time.sleep(2)
                continue
            break
    assert last_error is not None
    raise last_error


def _validate_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except Exception:  # noqa: BLE001
        return False


def download_binary(
    session: requests.Session,
    url: str,
    output_path: Path,
    *,
    timeout: int,
    headers: dict[str, str] | None = None,
) -> tuple[bool, str]:
    temp_path = output_path.with_suffix(f"{output_path.suffix}.part")
    if temp_path.exists():
        temp_path.unlink()

    try:
        response = request_with_retry(
            session,
            "GET",
            url,
            timeout=timeout,
            headers=headers,
            stream=True,
            allow_redirects=True,
        )
        content_type = (response.headers.get("Content-Type") or "").lower()
        content_disposition = (response.headers.get("Content-Disposition") or "").lower()
        if "pdf" not in content_type and "pdf" not in content_disposition and not url.lower().endswith(".pdf"):
            return False, f"响应不是 PDF: {content_type or 'unknown'}"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with temp_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 64):
                if chunk:
                    handle.write(chunk)

        if not _validate_pdf(temp_path):
            temp_path.unlink(missing_ok=True)
            return False, "下载结果不是有效 PDF"

        temp_path.replace(output_path)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        temp_path.unlink(missing_ok=True)
        return False, str(exc)


def candidate_output_path(row: dict, output_dir: Path) -> Path:
    return output_dir / f"{slugify(row['title'])}.pdf"


def try_download_from_arxiv(paper_info: dict, config: dict) -> tuple[Path | None, str]:
    title = paper_info["title"]
    search = arxiv.Search(query=f'ti:"{title}"', max_results=3, sort_by=arxiv.SortCriterion.Relevance)
    output_path = candidate_output_path(paper_info, config["output_dir"])

    for result in search.results():
        if title_similarity(title, result.title or "") < TITLE_MATCH_THRESHOLD:
            continue
        result.download_pdf(dirpath=str(config["output_dir"]), filename=output_path.name)
        if _validate_pdf(output_path):
            return output_path, ""
        output_path.unlink(missing_ok=True)
        return None, "arXiv 返回内容不是有效 PDF"
    return None, "未匹配到 arXiv 公开版本"


def try_download_from_semantic_scholar(paper_info: dict, config: dict) -> tuple[Path | None, str]:
    session = config["session"]
    doi = (paper_info.get("doi") or "").strip()
    title = paper_info["title"].strip()
    headers: dict[str, str] = {}
    if config.get("s2_api_key"):
        headers["x-api-key"] = config["s2_api_key"]

    pdf_url = ""
    try:
        if doi:
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi)}"
            response = request_with_retry(
                session,
                "GET",
                url,
                timeout=config["fast_timeout"],
                headers=headers,
                params={"fields": "title,openAccessPdf"},
            )
            data = response.json()
            pdf_url = ((data.get("openAccessPdf") or {}).get("url") or "").strip()
        else:
            response = request_with_retry(
                session,
                "GET",
                "https://api.semanticscholar.org/graph/v1/paper/search",
                timeout=config["fast_timeout"],
                headers=headers,
                params={"query": title, "fields": "title,openAccessPdf", "limit": 3},
            )
            candidates = response.json().get("data") or []
            for item in candidates:
                if title_similarity(title, item.get("title") or "") >= TITLE_MATCH_THRESHOLD:
                    pdf_url = ((item.get("openAccessPdf") or {}).get("url") or "").strip()
                    if pdf_url:
                        break
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("[Semantic Scholar] %s", exc)
        return None, str(exc)

    if not pdf_url:
        return None, "Semantic Scholar 未返回开放 PDF"

    output_path = candidate_output_path(paper_info, config["output_dir"])
    ok, message = download_binary(session, pdf_url, output_path, timeout=config["fast_timeout"])
    if ok:
        return output_path, ""
    return None, message


def query_unpaywall(session: requests.Session, doi: str, email: str, timeout: int) -> str:
    url = f"https://api.unpaywall.org/v2/{quote(doi)}?email={quote(email)}"
    response = request_with_retry(session, "GET", url, timeout=timeout)
    data = response.json()
    best = data.get("best_oa_location") or {}
    pdf_url = best.get("url_for_pdf") or ""
    if pdf_url:
        return pdf_url
    for location in data.get("oa_locations") or []:
        pdf_url = location.get("url_for_pdf") or ""
        if pdf_url:
            return pdf_url
    return ""


def try_download_from_unpaywall(paper_info: dict, config: dict) -> tuple[Path | None, str]:
    doi = (paper_info.get("doi") or "").strip()
    email = config.get("unpaywall_email", "").strip()
    if not doi:
        return None, "缺少 DOI，无法查 Unpaywall"
    if not email:
        return None, "未提供 Unpaywall 查询邮箱，跳过"

    try:
        pdf_url = query_unpaywall(config["session"], doi, email, config["fast_timeout"])
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("[Unpaywall] %s", exc)
        return None, str(exc)

    if not pdf_url:
        return None, "Unpaywall 未返回 PDF"

    output_path = candidate_output_path(paper_info, config["output_dir"])
    ok, message = download_binary(config["session"], pdf_url, output_path, timeout=config["fast_timeout"])
    if ok:
        return output_path, ""
    return None, message


def has_open_license(data: dict) -> bool:
    for item in data.get("message", {}).get("license") or []:
        url = (item.get("URL") or item.get("url") or "").lower()
        if "creativecommons.org" in url or "/licenses/" in url:
            return True
    return False


def try_download_from_crossref(paper_info: dict, config: dict) -> tuple[Path | None, str]:
    doi = (paper_info.get("doi") or "").strip()
    if not doi:
        return None, "缺少 DOI，无法查 Crossref"

    headers = {}
    if config.get("unpaywall_email"):
        headers["User-Agent"] = f"{USER_AGENT} mailto:{config['unpaywall_email']}"

    try:
        response = request_with_retry(
            config["session"],
            "GET",
            f"https://api.crossref.org/works/{quote(doi)}",
            timeout=config["fast_timeout"],
            headers=headers or None,
        )
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("[Crossref] %s", exc)
        return None, str(exc)

    if not has_open_license(data):
        return None, "Crossref 未返回可确认的开放许可"

    links = data.get("message", {}).get("link") or []
    candidate_urls = []
    for item in links:
        if (item.get("content-type") or "").lower() != "application/pdf":
            continue
        intended = (item.get("intended-application") or "").lower()
        if intended and intended not in {"text-mining", "similarity-checking"}:
            continue
        url = (item.get("URL") or item.get("url") or "").strip()
        if url:
            candidate_urls.append(url)

    if not candidate_urls:
        return None, "Crossref 未返回 PDF link"

    output_path = candidate_output_path(paper_info, config["output_dir"])
    for url in candidate_urls:
        ok, message = download_binary(config["session"], url, output_path, timeout=config["fast_timeout"], headers=headers or None)
        if ok:
            return output_path, ""
        LOGGER.debug("[Crossref] 下载失败: %s", message)
    return None, "Crossref PDF link 下载失败"


def pick_core_download_url(result: dict) -> str:
    direct = (result.get("downloadUrl") or "").strip()
    if direct:
        return direct
    links = result.get("links") or []
    if isinstance(links, dict):
        links = links.values()
    for item in links:
        if isinstance(item, str) and item.lower().endswith(".pdf"):
            return item
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or item.get("downloadUrl") or "").strip()
        if url and ((item.get("type") or "").lower() == "application/pdf" or url.lower().endswith(".pdf")):
            return url
    return ""


def try_download_from_core(paper_info: dict, config: dict) -> tuple[Path | None, str]:
    api_key = (config.get("core_api_key") or "").strip()
    if not api_key:
        return None, "未提供 CORE API Key，跳过"

    doi = (paper_info.get("doi") or "").strip()
    title = paper_info["title"].strip()
    query = f'doi:"{doi}"' if doi else f'title:"{title}"'

    try:
        response = request_with_retry(
            config["session"],
            "GET",
            "https://api.core.ac.uk/v3/search/works",
            timeout=config["deep_timeout"],
            headers={"Authorization": f"Bearer {api_key}"},
            params={"q": query, "limit": 3},
        )
        results = response.json().get("results") or []
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("[CORE] %s", exc)
        return None, str(exc)

    for item in results:
        if not doi and title_similarity(title, item.get("title") or "") < TITLE_MATCH_THRESHOLD:
            continue
        pdf_url = pick_core_download_url(item)
        if not pdf_url:
            continue
        output_path = candidate_output_path(paper_info, config["output_dir"])
        ok, message = download_binary(
            config["session"],
            pdf_url,
            output_path,
            timeout=config["deep_timeout"],
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if ok:
            return output_path, ""
        LOGGER.debug("[CORE] 下载失败: %s", message)
    return None, "CORE 未返回可用 PDF"


def try_download_from_pmc(paper_info: dict, config: dict) -> tuple[Path | None, str]:
    doi = (paper_info.get("doi") or "").strip()
    title = paper_info["title"].strip()
    query = doi if doi else title
    search_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    try:
        response = request_with_retry(
            config["session"],
            "GET",
            search_url,
            timeout=config["deep_timeout"],
            params={"query": query, "format": "json", "pageSize": 5},
        )
        results = response.json().get("resultList", {}).get("result", [])
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("[PMC] %s", exc)
        return None, str(exc)

    pmcid = ""
    for item in results:
        candidate_title = item.get("title") or ""
        if doi and (item.get("doi") or "").lower() == doi.lower():
            pmcid = item.get("pmcid") or ""
            break
        if title_similarity(title, candidate_title) >= TITLE_MATCH_THRESHOLD:
            pmcid = item.get("pmcid") or ""
            break

    if not pmcid:
        return None, "PMC 未匹配到公开全文"

    output_path = candidate_output_path(paper_info, config["output_dir"])
    ok, message = download_binary(
        config["session"],
        f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/",
        output_path,
        timeout=config["deep_timeout"],
    )
    if ok:
        return output_path, ""
    return None, message


def try_download_from_openalex(paper_info: dict, config: dict) -> tuple[Path | None, str]:
    doi = (paper_info.get("doi") or "").strip()
    title = paper_info["title"].strip()
    params = {}
    if config.get("unpaywall_email"):
        params["mailto"] = config["unpaywall_email"]

    try:
        if doi:
            response = request_with_retry(
                config["session"],
                "GET",
                f"https://api.openalex.org/works/doi:{quote(doi)}",
                timeout=config["deep_timeout"],
                params=params or None,
            )
            data = response.json()
        else:
            query_params = dict(params)
            query_params.update({"filter": f"title.search:{title}", "per-page": 3})
            response = request_with_retry(
                config["session"],
                "GET",
                "https://api.openalex.org/works",
                timeout=config["deep_timeout"],
                params=query_params,
            )
            results = response.json().get("results") or []
            data = {}
            for item in results:
                if title_similarity(title, item.get("display_name") or "") >= TITLE_MATCH_THRESHOLD:
                    data = item
                    break
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("[OpenAlex] %s", exc)
        return None, str(exc)

    open_access = data.get("open_access") or {}
    if not open_access.get("is_oa"):
        return None, "OpenAlex 标记为非开放获取"

    pdf_url = (open_access.get("oa_url") or "").strip()
    if not pdf_url:
        return None, "OpenAlex 未返回 oa_url"

    output_path = candidate_output_path(paper_info, config["output_dir"])
    ok, message = download_binary(config["session"], pdf_url, output_path, timeout=config["deep_timeout"])
    if ok:
        return output_path, ""
    return None, message


def chemrxiv_candidate_url(item: dict) -> str:
    for key in ("asset", "assets"):
        assets = item.get(key)
        if not assets:
            continue
        if isinstance(assets, dict):
            assets = [assets]
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            original = asset.get("original") or {}
            if isinstance(original, dict):
                url = (original.get("url") or "").strip()
                if url:
                    return url
            url = (asset.get("url") or "").strip()
            if url:
                return url
    return ""


def try_download_from_chemrxiv(paper_info: dict, config: dict) -> tuple[Path | None, str]:
    if not config.get("enable_chemrxiv", True):
        return None, "chemRxiv 已禁用"

    title = paper_info["title"].strip()
    try:
        response = request_with_retry(
            config["session"],
            "GET",
            "https://chemrxiv.org/engage/chemrxiv/public-api/v1/items",
            timeout=config["deep_timeout"],
            params={"searchTerm": title},
        )
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("[chemRxiv] %s", exc)
        return None, str(exc)

    candidates = []
    if isinstance(payload, dict):
        for key in ("items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break

    for item in candidates:
        candidate_title = item.get("title") or item.get("itemTitle") or ""
        if title_similarity(title, candidate_title) < TITLE_MATCH_THRESHOLD:
            continue
        pdf_url = chemrxiv_candidate_url(item)
        if not pdf_url:
            continue
        output_path = candidate_output_path(paper_info, config["output_dir"])
        ok, message = download_binary(config["session"], pdf_url, output_path, timeout=config["deep_timeout"])
        if ok:
            return output_path, ""
        LOGGER.debug("[chemRxiv] 下载失败: %s", message)
    return None, "chemRxiv 未返回可用 PDF"


FAST_TIER = [
    ("arXiv", try_download_from_arxiv),
    ("Semantic Scholar", try_download_from_semantic_scholar),
    ("Unpaywall", try_download_from_unpaywall),
    ("Crossref", try_download_from_crossref),
]

DEEP_TIER = [
    ("CORE", try_download_from_core),
    ("PMC", try_download_from_pmc),
    ("OpenAlex", try_download_from_openalex),
    ("chemRxiv", try_download_from_chemrxiv),
]


def _channel_available(name: str, config: dict) -> bool:
    if name == "Unpaywall" and not config.get("unpaywall_email"):
        return False
    if name == "CORE" and not config.get("core_api_key"):
        return False
    if name == "chemRxiv" and not config.get("enable_chemrxiv", True):
        return False
    return True


def try_download(
    session: requests.Session,
    row: dict,
    output_dir: Path,
    target_round: str,
    config: dict,
) -> dict:
    title = row["title"]
    base = {
        "title": title,
        "doi": row.get("doi", ""),
        "priority": row.get("priority", ""),
        "score_total": row.get("score_total", ""),
        "target_round": target_round,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "saved_path": "",
    }

    output_path = candidate_output_path(row, output_dir)
    if output_path.exists():
        return {
            **base,
            "status": "already_downloaded",
            "source": "local",
            "message": "目标文件已存在",
            "saved_path": str(output_path),
        }

    runtime = {
        **config,
        "session": session,
        "output_dir": output_dir,
    }
    failure_messages: list[str] = []

    for source_name, func in FAST_TIER:
        if not _channel_available(source_name, runtime):
            continue
        try:
            path, message = func(row, runtime)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("[%s] 未捕获异常: %s", source_name, exc)
            path, message = None, str(exc)
        if path is not None:
            return {
                **base,
                "status": "downloaded",
                "source": source_name,
                "message": "",
                "saved_path": str(path),
            }
        if message:
            failure_messages.append(f"{source_name}: {message}")

    if runtime.get("fast_only"):
        return {
            **base,
            "status": "manual_download_needed",
            "source": "",
            "message": " | ".join(failure_messages[:4]),
            "saved_path": "",
        }

    for source_name, func in DEEP_TIER:
        if not _channel_available(source_name, runtime):
            continue
        try:
            path, message = func(row, runtime)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("[%s] 未捕获异常: %s", source_name, exc)
            path, message = None, str(exc)
        if path is not None:
            return {
                **base,
                "status": "downloaded",
                "source": source_name,
                "message": "",
                "saved_path": str(path),
            }
        if message:
            failure_messages.append(f"{source_name}: {message}")

    return {
        **base,
        "status": "manual_download_needed",
        "source": "",
        "message": " | ".join(failure_messages[:6]),
        "saved_path": "",
    }


def main() -> int:
    paths = build_paths()

    parser = argparse.ArgumentParser(description="尝试通过开放获取渠道下载论文")
    parser.add_argument("--queue-csv", default=str(paths["reading_queue_csv"]), help="待读队列 CSV")
    parser.add_argument("--output-dir", default=str(paths["papers_round_1"]), help="下载目录")
    parser.add_argument("--download-log", default=str(paths["download_log_csv"]), help="下载日志 CSV")
    parser.add_argument("--top-n", type=int, default=10, help="最多尝试下载前 N 篇")
    parser.add_argument("--unpaywall-email", default="", help="Unpaywall 邮箱；同时用于 Crossref/OpenAlex polite pool")
    parser.add_argument("--s2-api-key", default="", help="可选的 Semantic Scholar API Key")
    parser.add_argument("--core-api-key", default="", help="可选的 CORE API Key；为空则跳过 CORE")
    parser.add_argument(
        "--enable-chemrxiv",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否启用 chemRxiv 渠道（默认启用）",
    )
    parser.add_argument("--fast-only", action="store_true", help="只运行快速层，不进入深度层")
    parser.add_argument("--timeout", type=int, default=30, help="兼容旧参数；用于限制最大请求超时秒数")
    parser.add_argument("--target-round", default="round_1", help="当前下载对应的目标轮次标签")
    parser.add_argument("--summary-md", default="", help="可选的本轮 Markdown 摘要输出路径")
    parser.add_argument("--summary-json", default="", help="可选的本轮 JSON 摘要输出路径")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    queue_rows = read_queue(Path(args.queue_csv))
    if not queue_rows:
        print(f"待读队列为空: {args.queue_csv}")
        write_log(Path(args.download_log), [])
        if args.summary_md:
            write_summary_markdown(Path(args.summary_md), args.target_round, [])
        if args.summary_json:
            write_summary_json(Path(args.summary_json), args.target_round, [])
        return 0

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "unpaywall_email": args.unpaywall_email.strip(),
        "s2_api_key": args.s2_api_key.strip(),
        "core_api_key": args.core_api_key.strip(),
        "enable_chemrxiv": bool(args.enable_chemrxiv),
        "fast_only": bool(args.fast_only),
        "fast_timeout": min(args.timeout, FAST_TIMEOUT) if args.timeout > 0 else FAST_TIMEOUT,
        "deep_timeout": min(max(args.timeout, FAST_TIMEOUT), DEEP_TIMEOUT) if args.timeout > 0 else DEEP_TIMEOUT,
    }

    session = build_session()
    logs = []
    selected_rows = queue_rows[: args.top_n]

    for index, row in enumerate(selected_rows, start=1):
        print(f"[{index}/{len(selected_rows)}] 尝试下载: {row['title']}")
        result = try_download(session, row, output_dir, args.target_round, config)
        logs.append(result)
        print(
            f"  -> {result['status']}"
            + (f" ({result['source']})" if result["source"] else "")
            + (f" | {result['message']}" if result["message"] else "")
        )

    write_log(Path(args.download_log), logs)
    if args.summary_md:
        write_summary_markdown(Path(args.summary_md), args.target_round, logs)
    if args.summary_json:
        write_summary_json(Path(args.summary_json), args.target_round, logs)

    downloaded = sum(1 for row in logs if row["status"] == "downloaded")
    manual = sum(1 for row in logs if row["status"] == "manual_download_needed")
    print(f"成功下载: {downloaded}")
    print(f"需人工处理: {manual}")
    print(f"下载日志: {args.download_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
