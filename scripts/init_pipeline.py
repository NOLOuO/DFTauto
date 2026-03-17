from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROUND_LABELS = ["round_1", "round_2", "round_3"]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def review_root() -> Path:
    return repo_root() / "literature-review"


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or (review_root() / "config.json")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_paths() -> dict[str, Path]:
    root = review_root()
    paths = {
        "review_root": root,
        "input_dir": root / "input",
        "input_papers_dir": root / "input" / "papers",
        "input_notes_dir": root / "input" / "notes",
        "papers_seed": root / "input" / "papers" / "seed",
        "output_dir": root / "output",
        "downloads_dir": root / "output" / "downloads",
        "parsed_dir": root / "output" / "parsed",
        "notes_dir": root / "output" / "notes",
        "records_dir": root / "output" / "records",
        "queue_dir": root / "output" / "records" / "queue",
        "graph_dir": root / "output" / "records" / "graph",
        "reports_dir": root / "output" / "reports",
        "round_logs_dir": root / "output" / "records" / "round_logs",
        "parsed_seed": root / "output" / "parsed" / "seed",
        "parsed_seed_info": root / "output" / "parsed" / "seed_info",
        "notes_seed": root / "output" / "notes" / "seed",
        "config_json": root / "config.json",
        "checkpoint": root / "output" / "records" / ".pipeline_checkpoint.json",
        "reading_queue_csv": root / "output" / "records" / "queue" / "reading_queue.csv",
        "download_log_csv": root / "output" / "records" / "queue" / "download_log.csv",
        "concept_map_json": root / "output" / "records" / "graph" / "concept_map.json",
        "citation_network_json": root / "output" / "records" / "graph" / "citation_network.json",
        "review_summary_md": root / "output" / "reports" / "review_summary.md",
        "knowledge_gaps_md": root / "output" / "reports" / "knowledge_gaps.md",
        "literature_review_xlsx": root / "output" / "reports" / "literature_review.xlsx",
        "llm_refine_log_jsonl": root / "output" / "records" / "llm_refine_log.jsonl",
    }
    for round_label in ROUND_LABELS:
        paths[f"papers_{round_label}"] = root / "output" / "downloads" / round_label
        paths[f"parsed_{round_label}"] = root / "output" / "parsed" / round_label
        paths[f"parsed_{round_label}_info"] = root / "output" / "parsed" / f"{round_label}_info"
        paths[f"notes_{round_label}"] = root / "output" / "notes" / round_label
        paths[f"{round_label}_queue_csv"] = root / "output" / "records" / "queue" / f"{round_label}_reading_queue.csv"
        paths[f"{round_label}_download_log_csv"] = root / "output" / "records" / "queue" / f"{round_label}_download_log.csv"
        paths[f"{round_label}_download_summary_md"] = root / "output" / "records" / "round_logs" / f"{round_label}_download_summary.md"
        paths[f"{round_label}_download_summary_json"] = root / "output" / "records" / "round_logs" / f"{round_label}_download_summary.json"
    return paths


def info_dir_key(round_label: str) -> str:
    if round_label == "seed":
        return "parsed_seed_info"
    return f"parsed_{round_label}_info"


def existing_input_dirs(paths: dict[str, Path], rounds: list[str] | None = None) -> list[Path]:
    result = []
    keys = ["parsed_seed_info"]
    for round_label in rounds or ROUND_LABELS:
        keys.append(info_dir_key(round_label))
    for key in keys:
        path = paths[key]
        if path.exists() and any(path.glob("*.json")):
            result.append(path)
    return result


def count_files(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.glob(pattern))
