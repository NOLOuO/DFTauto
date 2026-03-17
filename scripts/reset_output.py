from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from init_pipeline import ROUND_LABELS, build_paths, review_root


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def recreate_layout(paths: dict[str, Path]) -> None:
    required_dirs = [
        paths["input_dir"],
        paths["input_papers_dir"],
        paths["input_notes_dir"],
        paths["papers_seed"],
        paths["output_dir"],
        paths["downloads_dir"],
        paths["parsed_dir"],
        paths["notes_dir"],
        paths["records_dir"],
        paths["queue_dir"],
        paths["graph_dir"],
        paths["reports_dir"],
        paths["round_logs_dir"],
        paths["parsed_seed"],
        paths["parsed_seed_info"],
        paths["notes_seed"],
    ]
    for round_label in ROUND_LABELS:
        required_dirs.extend(
            [
                paths[f"papers_{round_label}"],
                paths[f"parsed_{round_label}"],
                paths[f"parsed_{round_label}_info"],
                paths[f"notes_{round_label}"],
            ]
        )
    for path in required_dirs:
        path.mkdir(parents=True, exist_ok=True)


def reset_output() -> None:
    root = review_root()
    paths = build_paths()

    removable = [
        root / "__pycache__",
        root / "scripts" / "__pycache__",
        root / "backups",
        root / "graph",
        root / "notes",
        root / "papers",
        root / "parsed",
        root / "queue",
        paths["output_dir"],
    ]
    for path in removable:
        remove_path(path)

    recreate_layout(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description="清空 literature-review 的输出内容并重建目录")
    _ = parser.parse_args()
    reset_output()
    print("output 已清空并重建，input/seed 已保留。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
