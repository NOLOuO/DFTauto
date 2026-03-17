import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from init_pipeline import build_paths


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


def load_seed_papers(input_dirs: list[Path]) -> list[dict]:
    papers = []
    for input_dir in input_dirs:
        for path in sorted(input_dir.glob("*.json")):
            papers.append(json.loads(path.read_text(encoding="utf-8")))
    return papers


def aggregate_references(papers: list[dict]) -> tuple[list[dict], list[dict]]:
    aggregated = {}
    rows = []

    for paper in papers:
        source_title = paper.get("title", "")
        source_doi = paper.get("doi", "")
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
                    "first_author": ref.get("first_author", "").strip(),
                    "year": (ref.get("year") or "").strip(),
                    "journal": (ref.get("journal") or "").strip(),
                    "cited_by_count": 0,
                    "cited_by_titles": [],
                    "cited_by_dois": [],
                },
            )

            if source_title not in entry["cited_by_titles"]:
                entry["cited_by_titles"].append(source_title)
                entry["cited_by_count"] += 1
            if source_doi and source_doi not in entry["cited_by_dois"]:
                entry["cited_by_dois"].append(source_doi)

            row = {
                "source_paper_title": source_title,
                "source_paper_doi": source_doi,
                "ref_key": key,
                "ref_title": ref.get("title", "").strip(),
                "ref_doi": (ref.get("doi") or "").strip(),
                "ref_first_author": (ref.get("first_author") or "").strip(),
                "ref_year": (ref.get("year") or "").strip(),
                "ref_journal": (ref.get("journal") or "").strip(),
            }
            rows.append(row)

    refs = sorted(
        aggregated.values(),
        key=lambda item: (-item["cited_by_count"], item["year"], item["title"].lower()),
    )
    return refs, rows


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    paths = build_paths()

    parser = argparse.ArgumentParser(description="汇总已读论文中的参考文献")
    parser.add_argument(
        "--input-dirs",
        nargs="+",
        default=[str(paths["parsed_seed_info"])],
        help="结构化论文 JSON 目录列表",
    )
    parser.add_argument(
        "--output-json",
        default=str(paths["queue_dir"] / "references_catalog.json"),
        help="汇总 JSON 输出路径",
    )
    parser.add_argument(
        "--output-csv",
        default=str(paths["queue_dir"] / "references_catalog.csv"),
        help="汇总 CSV 输出路径",
    )
    parser.add_argument(
        "--detail-csv",
        default=str(paths["queue_dir"] / "reference_mentions.csv"),
        help="逐条引用明细 CSV 输出路径",
    )
    args = parser.parse_args()

    input_dirs = [Path(item) for item in args.input_dirs]
    papers = load_seed_papers(input_dirs)
    if not papers:
        print(f"未找到结构化论文 JSON: {input_dirs}")
        return 1

    refs, rows = aggregate_references(papers)

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "paper_count": len(papers),
                "reference_count": len(refs),
                "references": refs,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    write_csv(
        Path(args.output_csv),
        refs,
        [
            "ref_key",
            "title",
            "doi",
            "first_author",
            "year",
            "journal",
            "cited_by_count",
            "cited_by_titles",
            "cited_by_dois",
        ],
    )
    write_csv(
        Path(args.detail_csv),
        rows,
        [
            "source_paper_title",
            "source_paper_doi",
            "ref_key",
            "ref_title",
            "ref_doi",
            "ref_first_author",
            "ref_year",
            "ref_journal",
        ],
    )

    print(f"已读论文数: {len(papers)}")
    print(f"参考文献去重数: {len(refs)}")
    print(f"汇总 JSON: {output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
