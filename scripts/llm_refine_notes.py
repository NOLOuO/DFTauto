from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

from init_pipeline import build_paths
from llm_provider_presets import build_provider_model_config, default_model_id, list_provider_ids


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SECTION_PRIORITY = [
    "abstract",
    "introduction",
    "background",
    "related work",
    "method",
    "methods",
    "approach",
    "architecture",
    "framework",
    "implementation",
    "experiment",
    "evaluation",
    "results",
    "discussion",
    "limitation",
    "conclusion",
]

NOTE_TEMPLATE = """# [论文标题]

## 元信息
- **作者**: [第一作者 et al., 年份]
- **期刊/来源**: [期刊名 或 arXiv ID]
- **DOI**: [DOI；若疑似错误请明确写出“需人工核对”]
- **精读日期**: [YYYY-MM-DD]
- **来源轮次**: seed / round_1 / round_2

## 一句话总结
[用一句话概括核心贡献，不超过 80 字]

## 核心问题
[说明论文试图解决的问题、现实背景与难点]

## 技术方案
### 系统架构
[说明整体系统结构、角色分工、工具/协议层设计]

### 关键技术细节
- [技术点 1]
- [技术点 2]
- [技术点 3]

### 使用的 LLM
[如果未明确说明，就写“未在已提取内容中明确说明”]

## 实验与验证
- **Benchmark**: [数据集/任务/案例]
- **关键指标**: [成功率、误差、与专家对比等]
- **计算开销**: [token、API 调用次数、时间；若无则明确写待补充]

## 与本项目的关联
### 可直接复用的设计
- [可迁移设计 1]
- [可迁移设计 2]

### 本文的局限性
- [局限 1]
- [局限 2]

### 对我们系统的启发
- [启发 1]
- [启发 2]

## 关键参考文献（值得追踪）
- [参考文献标题] — 理由: ...
- [参考文献标题] — 理由: ...

## 与其他已读论文的关系
[若上下文不足，可明确写“待结合其他论文进一步补充”]
"""


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def clip_text(text: str, max_chars: int) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def infer_round_label(input_dir: Path, notes_dir: Path) -> str:
    for part in list(input_dir.parts)[::-1] + list(notes_dir.parts)[::-1]:
        if part in {"seed", "round_1", "round_2", "round_3"}:
            return part
    return "seed"


def section_rank(title: str) -> tuple[int, str]:
    normalized = clean_text(title).lower()
    for index, keyword in enumerate(SECTION_PRIORITY):
        if keyword in normalized:
            return (index, normalized)
    return (len(SECTION_PRIORITY), normalized)


def select_sections(paper: dict[str, Any], max_section_chars: int, max_total_chars: int) -> list[dict[str, str]]:
    sections = paper.get("sections", []) or []
    ranked = sorted(sections, key=lambda item: section_rank(item.get("title", "")))

    chosen: list[dict[str, str]] = []
    total_chars = 0
    seen_titles: set[str] = set()

    for section in ranked:
        title = clean_text(section.get("title", "")) or "Untitled Section"
        normalized_title = title.lower()
        if normalized_title in seen_titles:
            continue
        text = clip_text(section.get("text", ""), max_section_chars)
        if not text:
            continue
        if total_chars + len(text) > max_total_chars and chosen:
            break
        chosen.append({"title": title, "text": text})
        total_chars += len(text)
        seen_titles.add(normalized_title)

    if not chosen and sections:
        first = sections[0]
        chosen.append(
            {
                "title": clean_text(first.get("title", "")) or "Untitled Section",
                "text": clip_text(first.get("text", ""), max_section_chars),
            }
        )
    return chosen


def select_references(paper: dict[str, Any], max_refs: int) -> list[dict[str, str]]:
    refs = paper.get("references", []) or []
    scored: list[tuple[int, dict[str, str]]] = []
    for ref in refs:
        title = clean_text(ref.get("title", ""))
        if not title:
            continue
        score = 0
        lower = title.lower()
        for keyword in ("agent", "llm", "workflow", "dft", "vasp", "automation", "materials"):
            if keyword in lower:
                score += 2
        if ref.get("doi"):
            score += 1
        if ref.get("year"):
            score += 1
        scored.append(
            (
                -score,
                {
                    "title": title,
                    "first_author": clean_text(ref.get("first_author", "")),
                    "year": clean_text(ref.get("year", "")),
                    "doi": clean_text(ref.get("doi", "")),
                    "journal": clean_text(ref.get("journal", "")),
                },
            )
        )
    scored.sort(key=lambda item: (item[0], item[1]["title"].lower()))
    return [item[1] for item in scored[:max_refs]]


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required_fields = ["base_url", "api_key_env", "model"]
    missing = [field for field in required_fields if not clean_text(str(data.get(field, "")))]
    if missing:
        raise ValueError(f"配置缺少字段: {', '.join(missing)}")
    validate_base_url_policy(data)
    return data


def build_config_from_provider(provider_id: str, model_id: str | None = None) -> dict[str, Any]:
    config = build_provider_model_config(provider_id, model_id)
    validate_base_url_policy(config)
    return config


def is_allowed_host(host: str, allowed_hosts: list[str]) -> bool:
    normalized_host = (host or "").strip().lower()
    if not normalized_host:
        return False
    if normalized_host in {item.lower() for item in allowed_hosts}:
        return True
    if normalized_host == "localhost" or normalized_host.endswith(".cn"):
        return True
    try:
        ip = ipaddress.ip_address(normalized_host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


def validate_base_url_policy(config: dict[str, Any]) -> None:
    base_url = clean_text(config.get("base_url", ""))
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"base_url 必须使用 http/https: {base_url}")

    allowed_hosts = config.get("allowed_hosts") or []
    if allowed_hosts and not isinstance(allowed_hosts, list):
        raise ValueError("配置项 allowed_hosts 必须是数组")

    if not is_allowed_host(host, [str(item) for item in allowed_hosts]):
        raise ValueError(
            "根据当前项目约束，LLM 接口仅允许中国区或本地服务地址。"
            f"当前 host={host or 'unknown'} 不在允许范围内。"
        )


def extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(str(item["text"]))
                elif item.get("type") == "output_text" and item.get("text"):
                    parts.append(str(item["text"]))
        return "\n".join(parts).strip()
    return ""


def maybe_strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def build_runtime_config(
    provider: str,
    model_id: str | None = None,
    config_path: Path | None = None,
    api_key_value: str = "",
) -> dict[str, Any]:
    if config_path is not None:
        config = load_config(config_path)
    else:
        config = build_config_from_provider(provider, model_id)
    if api_key_value.strip():
        config["_api_key_override"] = api_key_value.strip()
    return config


def build_messages(
    paper: dict[str, Any],
    round_label: str,
    existing_note: str,
    section_payload: list[dict[str, str]],
    ref_payload: list[dict[str, str]],
    config: dict[str, Any],
) -> list[dict[str, str]]:
    thinking_instruction = clean_text(config.get("thinking_instruction", ""))
    system_lines = [
        "你是 DFT 自动化方向的文献精读代理。",
        "任务是基于提供的结构化论文内容，把当前论文整理成高质量中文 Markdown 精读笔记。",
        "必须严格遵守给定标题层级与模板结构。",
        "如果信息不足，明确写“未在已提取内容中明确说明”或“待补充”，不要编造。",
        "如果 DOI、作者、来源与题目明显冲突，必须直说“需人工核对”。",
        "不要输出 JSON，不要输出代码块围栏，不要输出思考过程，只输出最终 Markdown。",
    ]
    if config.get("thinking_enabled", True):
        system_lines.append(
            thinking_instruction
            or "请先进行充分的内部分析、交叉核对和逐段推理，再输出最终答案，但不要展示思考过程。"
        )

    user_payload = {
        "task": "请把这篇论文整理为精修版 Markdown 笔记。",
        "round_label": round_label,
        "template": NOTE_TEMPLATE,
        "paper_meta": {
            "source_file": paper.get("source_file", ""),
            "title": clean_text(paper.get("title", "")),
            "abstract": clean_text(paper.get("abstract", "")),
            "authors": paper.get("authors", []),
            "doi": clean_text(paper.get("doi", "")),
        },
        "selected_sections": section_payload,
        "selected_references": ref_payload,
        "existing_note_draft": existing_note or "",
        "requirements": [
            "优先利用 existing_note_draft 中已经正确的信息，但必须以 paper_meta 和 selected_sections 为准。",
            "输出必须保留模板中的所有二级、三级标题。",
            "“与本项目的关联”必须尽量落到 Agent、结构建模、输入生成、HPC 调度、错误恢复、结果判读这些维度。",
            "“关键参考文献”优先选与 Agent / LLM / DFT / workflow / automation 高相关的条目。",
            "全文用中文写，模型名、软件名、协议名保留英文原名。",
        ],
    }

    return [
        {"role": "system", "content": "\n".join(system_lines)},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
    ]


def call_openai_compatible_api(config: dict[str, Any], messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    api_key = clean_text(config.get("_api_key_override", "")) or os.getenv(config["api_key_env"], "").strip()
    if not api_key:
        raise RuntimeError(f"环境变量 {config['api_key_env']} 未设置，无法调用 LLM 接口")

    base_url = config["base_url"].rstrip("/")
    url = f"{base_url}/chat/completions"

    body: dict[str, Any] = {
        "model": config["model"],
        "messages": messages,
    }
    if config.get("supports_temperature") and "temperature" in config:
        body["temperature"] = config["temperature"]
    if "max_tokens" in config:
        body["max_tokens"] = config["max_tokens"]
    if config.get("supports_top_p") and "top_p" in config:
        body["top_p"] = config["top_p"]

    extra_body = config.get("extra_body") or {}
    if not isinstance(extra_body, dict):
        raise ValueError("配置项 extra_body 必须是 JSON object")
    body.update(extra_body)

    request_overrides = config.get("request_overrides") or {}
    if not isinstance(request_overrides, dict):
        raise ValueError("配置项 request_overrides 必须是 JSON object")
    body.update(request_overrides)

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    timeout_seconds = int(config.get("timeout_seconds", 180))
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM 接口返回 HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"LLM 接口连接失败: {exc}") from exc

    data = json.loads(raw)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM 接口未返回 choices: {raw}")

    message = choices[0].get("message") or {}
    content = maybe_strip_markdown_fence(extract_message_text(message.get("content")))
    if not content:
        raise RuntimeError(f"LLM 接口未返回可写入内容: {raw}")

    meta = {
        "model": data.get("model", config["model"]),
        "id": data.get("id", ""),
        "created": data.get("created"),
        "usage": data.get("usage", {}),
        "reasoning_content_present": bool(message.get("reasoning_content") or message.get("reasoning_details")),
        "service_tier": data.get("service_tier", ""),
    }
    return content, meta


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def refine_notes(
    *,
    input_dir: Path,
    notes_dir: Path,
    output_dir: Path,
    config: dict[str, Any],
    limit: int = 0,
    max_section_chars: int = 1800,
    max_total_section_chars: int = 12000,
    max_refs: int = 12,
    overwrite: bool = False,
    dry_run: bool = False,
    log_path: Path,
    logger: Any | None = None,
) -> int:
    def emit(message: str) -> None:
        print(message)
        if logger is not None:
            logger(message)

    if not input_dir.exists():
        emit(f"输入目录不存在: {input_dir}")
        return 1

    round_label = infer_round_label(input_dir, notes_dir)
    json_files = sorted(input_dir.glob("*.json"))
    if limit > 0:
        json_files = json_files[:limit]
    if not json_files:
        emit(f"未找到结构化 JSON: {input_dir}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    emit(f"输入目录: {input_dir}")
    emit(f"初版笔记目录: {notes_dir}")
    emit(f"输出目录: {output_dir}")
    emit(f"模型: {config['model']}")
    emit(f"处理数量: {len(json_files)}")
    emit(f"来源轮次: {round_label}")

    success_count = 0
    skip_count = 0
    failure_count = 0

    for index, json_path in enumerate(json_files, start=1):
        paper = json.loads(json_path.read_text(encoding="utf-8"))
        stem = json_path.stem.replace(".tei", "")
        note_input_path = notes_dir / f"{stem}.md"
        note_output_path = output_dir / f"{stem}.md"

        if note_output_path.exists() and not overwrite:
            emit(f"[{index}/{len(json_files)}] 跳过: {note_output_path.name} 已存在")
            skip_count += 1
            continue

        existing_note = note_input_path.read_text(encoding="utf-8") if note_input_path.exists() else ""
        section_payload = select_sections(
            paper,
            max_section_chars=max_section_chars,
            max_total_chars=max_total_section_chars,
        )
        ref_payload = select_references(paper, max_refs=max_refs)
        messages = build_messages(
            paper=paper,
            round_label=round_label,
            existing_note=existing_note,
            section_payload=section_payload,
            ref_payload=ref_payload,
            config=config,
        )

        emit(f"[{index}/{len(json_files)}] 精修: {json_path.name} -> {note_output_path.name}")
        if dry_run:
            continue

        started_at = datetime.now().isoformat(timespec="seconds")
        try:
            refined_note, meta = call_openai_compatible_api(config, messages)
            note_output_path.write_text(refined_note.strip() + "\n", encoding="utf-8")
            append_jsonl(
                log_path,
                {
                    "timestamp": started_at,
                    "provider_id": config.get("provider_id", ""),
                    "source_json": str(json_path),
                    "draft_note": str(note_input_path) if note_input_path.exists() else "",
                    "output_note": str(note_output_path),
                    "model": meta.get("model", config["model"]),
                    "usage": meta.get("usage", {}),
                    "reasoning_content_present": meta.get("reasoning_content_present", False),
                    "status": "ok",
                },
            )
            success_count += 1
        except Exception as exc:  # noqa: BLE001
            append_jsonl(
                log_path,
                {
                    "timestamp": started_at,
                    "provider_id": config.get("provider_id", ""),
                    "source_json": str(json_path),
                    "draft_note": str(note_input_path) if note_input_path.exists() else "",
                    "output_note": str(note_output_path),
                    "model": config["model"],
                    "status": "error",
                    "error": str(exc),
                },
            )
            emit(f"  失败: {exc}")
            failure_count += 1

    emit("")
    emit(f"完成: 成功 {success_count} 篇, 跳过 {skip_count} 篇, 失败 {failure_count} 篇")
    emit(f"日志: {log_path}")
    return 0 if failure_count == 0 else 2


def main() -> int:
    paths = build_paths()

    parser = argparse.ArgumentParser(description="使用 OpenAI 兼容接口对论文笔记进行 LLM 精修")
    parser.add_argument(
        "--input-dir",
        default=str(paths["parsed_seed_info"]),
        help="结构化论文 JSON 输入目录",
    )
    parser.add_argument(
        "--notes-dir",
        default=str(paths["notes_seed"]),
        help="当前初版笔记目录，用作 draft 输入",
    )
    parser.add_argument(
        "--output-dir",
        default=str(paths["notes_seed"]),
        help="精修笔记输出目录",
    )
    parser.add_argument(
        "--provider",
        choices=list_provider_ids(),
        default="moonshot",
        help="预置国内模型服务提供方",
    )
    parser.add_argument(
        "--model-id",
        default="",
        help="provider 对应的模型 ID；为空时使用该 provider 的默认模型",
    )
    parser.add_argument(
        "--config",
        default="",
        help="可选的 LLM 配置文件路径；不提供时使用 provider 预置配置",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="直接传入 API Key；为空时从 provider 对应环境变量读取",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅处理前 N 篇，0 表示全部处理",
    )
    parser.add_argument(
        "--max-section-chars",
        type=int,
        default=1800,
        help="单个章节最多截取字符数",
    )
    parser.add_argument(
        "--max-total-section-chars",
        type=int,
        default=12000,
        help="所有章节合计最多截取字符数",
    )
    parser.add_argument(
        "--max-refs",
        type=int,
        default=12,
        help="提供给 LLM 的参考文献上限",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已存在的输出文件",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将处理的文件，不实际调用 LLM",
    )
    parser.add_argument(
        "--log-path",
        default=str(paths["llm_refine_log_jsonl"]),
        help="精修调用日志 JSONL 路径",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    notes_dir = Path(args.notes_dir)
    output_dir = Path(args.output_dir)
    config_path = Path(args.config) if args.config else None
    log_path = Path(args.log_path)
    if config_path is not None and not config_path.exists():
        print(f"配置文件不存在: {config_path}")
        return 1

    config = build_runtime_config(
        provider=args.provider,
        model_id=args.model_id or default_model_id(args.provider),
        config_path=config_path,
        api_key_value=args.api_key,
    )
    if config.get("supports_temperature") and "temperature" not in config:
        config["temperature"] = config.get("default_temperature")
    if config.get("supports_top_p") and "top_p" not in config:
        config["top_p"] = config.get("default_top_p")

    return refine_notes(
        input_dir=input_dir,
        notes_dir=notes_dir,
        output_dir=output_dir,
        config=config,
        limit=args.limit,
        max_section_chars=args.max_section_chars,
        max_total_section_chars=args.max_total_section_chars,
        max_refs=args.max_refs,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        log_path=log_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
