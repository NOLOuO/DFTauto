import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from init_pipeline import build_paths


RELEVANCE_KEYWORDS = {
    "agent": 1.6,
    "agentic": 1.6,
    "multi-agent": 1.8,
    "autonomous": 1.4,
    "llm": 1.5,
    "large language model": 1.5,
    "workflow": 1.2,
    "automation": 1.3,
    "dft": 1.5,
    "density functional theory": 1.5,
    "vasp": 1.1,
    "quantum espresso": 1.1,
    "hpc": 0.9,
    "convergence": 0.9,
    "materials": 0.5,
}

HIGH_IMPACT_VENUES = [
    "nature",
    "science",
    "pnas",
    "npj",
    "physical review",
    "journal of the american chemical society",
    "digital discovery",
    "machine learning: science and technology",
]


def normalize_text(value: str) -> str:
    value = value or ""
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def reference_key(ref: dict) -> str:
    doi = (ref.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    title = normalize_text(ref.get("title", ""))
    return f"title:{title}" if title else ""


def load_papers(input_dirs: list[Path]) -> list[dict]:
    papers = []
    for input_dir in input_dirs:
        papers.extend(
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(input_dir.glob("*.json"))
        )
    return papers


def relevance_score(title: str, journal: str) -> float:
    text = f"{title} {journal}".lower()
    score = 0.0
    for keyword, weight in RELEVANCE_KEYWORDS.items():
        if keyword in text:
            score += weight
    return min(score, 4.0)


def impact_score(journal: str, doi: str, cited_by_count: int) -> float:
    journal_lower = (journal or "").lower()
    score = 0.5
    if doi:
        score += 0.8
    if any(venue in journal_lower for venue in HIGH_IMPACT_VENUES):
        score += 1.0
    if cited_by_count >= 3:
        score += 0.7
    elif cited_by_count == 2:
        score += 0.4
    return min(score, 2.5)


def recency_score(year_value: str) -> float:
    try:
        year = int(year_value)
    except (TypeError, ValueError):
        return 0.4

    if year >= 2025:
        return 2.0
    if year >= 2023:
        return 1.6
    if year >= 2020:
        return 1.2
    if year >= 2015:
        return 0.8
    return 0.5


def accessibility_score(doi: str, title: str, journal: str) -> float:
    text = f"{title} {journal}".lower()
    if "arxiv" in text or "chemrxiv" in text:
        return 1.8
    if doi:
        return 1.2
    if journal:
        return 0.8
    return 0.4


def priority_label(score: float) -> str:
    if score >= 10:
        return "must_read"
    if score >= 8:
        return "recommended"
    return "backlog"


def load_existing_log(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result = {}
    for row in rows:
        key = (row.get("title", ""), (row.get("doi") or "").strip().lower())
        result[key] = row
    return result


def main() -> int:
    paths = build_paths()

    parser = argparse.ArgumentParser(description="对参考文献进行启发式评分")
    parser.add_argument(
        "--input-dirs",
        nargs="+",
        default=[str(paths["parsed_seed_info"])],
        help="结构化论文 JSON 目录列表",
    )
    parser.add_argument(
        "--output-csv",
        default=str(paths["reading_queue_csv"]),
        help="待读队列输出路径",
    )
    parser.add_argument(
        "--download-log",
        default=str(paths["download_log_csv"]),
        help="下载记录输出路径",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="输出前 N 篇高优先级候选",
    )
    parser.add_argument(
        "--target-round",
        default="round_1",
        help="当前候选对应的目标轮次标签",
    )
    args = parser.parse_args()

    papers = load_papers([Path(item) for item in args.input_dirs])
    if not papers:
        print(f"未找到结构化论文 JSON: {args.input_dirs}")
        return 1

    seed_dois = {
        (paper.get("doi") or "").strip().lower()
        for paper in papers
        if (paper.get("doi") or "").strip()
    }
    seed_titles = {
        normalize_text(paper.get("title", ""))
        for paper in papers
        if paper.get("title")
    }

    aggregated = {}
    title_to_sources = defaultdict(list)

    for paper in papers:
        source_title = paper.get("title", "")
        for ref in paper.get("references", []):
            key = reference_key(ref)
            if not key:
                continue
            entry = aggregated.setdefault(
                key,
                {
                    "ref_key": key,
                    "title": ref.get("title", "").strip(),
                    "doi": (ref.get("doi") or "").strip(),
                    "first_author": (ref.get("first_author") or "").strip(),
                    "year": (ref.get("year") or "").strip(),
                    "journal": (ref.get("journal") or "").strip(),
                    "cited_by_titles": [],
                },
            )
            if source_title not in entry["cited_by_titles"]:
                entry["cited_by_titles"].append(source_title)
            title_to_sources[entry["title"]].append(source_title)

    queue_rows = []
    for entry in aggregated.values():
        normalized_title = normalize_text(entry["title"])
        normalized_doi = (entry["doi"] or "").strip().lower()
        if normalized_doi in seed_dois or normalized_title in seed_titles:
            continue

        cited_by_count = len(entry["cited_by_titles"])
        co_citation = min(cited_by_count * 2.5, 5.0)
        relevance = relevance_score(entry["title"], entry["journal"])
        impact = impact_score(entry["journal"], entry["doi"], cited_by_count)
        recency = recency_score(entry["year"])
        accessibility = accessibility_score(entry["doi"], entry["title"], entry["journal"])
        total = round(co_citation + relevance + impact + recency + accessibility, 2)

        queue_rows.append(
            {
                "priority": priority_label(total),
                "score_total": total,
                "score_cocitation": round(co_citation, 2),
                "score_relevance": round(relevance, 2),
                "score_impact": round(impact, 2),
                "score_recency": round(recency, 2),
                "score_accessibility": round(accessibility, 2),
                "ref_key": entry["ref_key"],
                "title": entry["title"],
                "doi": entry["doi"],
                "first_author": entry["first_author"],
                "year": entry["year"],
                "journal": entry["journal"],
                "cited_by_count": cited_by_count,
                "cited_by_titles": " | ".join(entry["cited_by_titles"]),
                "status": "not_downloaded",
                "note": "启发式初筛；高分项优先在下一轮尝试下载",
            }
        )

    queue_rows.sort(
        key=lambda row: (
            {"must_read": 0, "recommended": 1, "backlog": 2}[row["priority"]],
            -row["score_total"],
            row["title"].lower(),
        )
    )
    top_rows = queue_rows[: args.top_n]

    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "priority",
        "score_total",
        "score_cocitation",
        "score_relevance",
        "score_impact",
        "score_recency",
        "score_accessibility",
        "ref_key",
        "title",
        "doi",
        "first_author",
        "year",
        "journal",
        "cited_by_count",
        "cited_by_titles",
        "status",
        "note",
    ]
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(top_rows)

    download_log = Path(args.download_log)
    existing_log = load_existing_log(download_log)
    download_rows = []
    for row in top_rows:
        key = (row["title"], (row["doi"] or "").strip().lower())
        previous = existing_log.get(key, {})
        download_rows.append(
            {
                "title": row["title"],
                "doi": row["doi"],
                "priority": row["priority"],
                "score_total": row["score_total"],
                "status": previous.get("status", "not_attempted"),
                "source": previous.get("source", ""),
                "target_round": previous.get("target_round", args.target_round),
                "timestamp": previous.get("timestamp", datetime.now().isoformat(timespec="seconds")),
                "message": previous.get("message", ""),
                "saved_path": previous.get("saved_path", ""),
            }
        )
    with download_log.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
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
            ],
        )
        writer.writeheader()
        writer.writerows(download_rows)

    print(f"候选参考文献总数: {len(queue_rows)}")
    print(f"输出前 {len(top_rows)} 篇到: {output_csv}")
    print(f"下载日志初始化完成: {download_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
