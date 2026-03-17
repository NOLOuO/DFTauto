import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

from init_pipeline import build_paths


KEYWORD_GROUPS = {
    "agent": ["agent", "agentic", "autonomous", "planner", "multi-agent"],
    "llm": ["llm", "large language model", "language model", "gpt", "claude", "gemini", "deepseek"],
    "dft": ["density functional theory", "dft", "quantum espresso", "vasp", "ab initio"],
    "workflow": ["workflow", "pipeline", "orchestration", "jobflow", "atomate", "aiida"],
    "structure_modeling": ["structure", "lattice", "slab", "adsorption", "geometry", "phonon"],
    "hpc": ["hpc", "slurm", "cluster", "parallelization", "mpi", "resource allocation"],
    "error_recovery": ["error", "recovery", "retry", "convergence", "troubleshooting", "self-correction"],
    "benchmark": ["benchmark", "dataset", "evaluation", "accuracy", "cost", "efficiency"],
    "materials_db": ["materials project", "aflow", "database", "pymatgen", "ase"],
    "memory": ["memory", "canvas", "history", "context", "retrieve"],
}


def normalize_text(value: str) -> str:
    value = value or ""
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def paper_key(paper: dict) -> str:
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    title = normalize_text(paper.get("title", ""))
    return f"title:{title}"


def reference_key(ref: dict) -> str:
    doi = (ref.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    title = normalize_text(ref.get("title", ""))
    return f"title:{title}" if title else ""


def load_json_files(input_dirs: list[Path]) -> list[dict]:
    papers = []
    for input_dir in input_dirs:
        papers.extend(
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(input_dir.glob("*.json"))
        )
    return papers


def extract_concepts(text: str) -> list[str]:
    text = (text or "").lower()
    found = []
    for concept, keywords in KEYWORD_GROUPS.items():
        if any(keyword in text for keyword in keywords):
            found.append(concept)
    return found


def main() -> int:
    paths = build_paths()

    parser = argparse.ArgumentParser(description="构建引用图谱和概念图谱")
    parser.add_argument(
        "--input-dirs",
        nargs="+",
        default=[str(paths["parsed_seed_info"])],
        help="结构化论文 JSON 目录列表",
    )
    parser.add_argument(
        "--citation-output",
        default=str(paths["citation_network_json"]),
        help="引用图输出路径",
    )
    parser.add_argument(
        "--concept-output",
        default=str(paths["concept_map_json"]),
        help="概念图输出路径",
    )
    args = parser.parse_args()

    papers = load_json_files([Path(item) for item in args.input_dirs])
    if not papers:
        print(f"未找到结构化论文 JSON: {args.input_dirs}")
        return 1

    citation_nodes = {}
    citation_edges = []
    reference_cited_by = defaultdict(set)

    concept_to_papers = defaultdict(set)
    concept_edge_papers = defaultdict(set)
    paper_concepts = []

    for paper in papers:
        pid = paper_key(paper)
        title = paper.get("title", "").strip()
        citation_nodes[pid] = {
            "id": pid,
            "type": "paper",
            "title": title,
            "doi": paper.get("doi", "").strip(),
            "authors": paper.get("authors", []),
            "round": "unknown",
            "reference_count": len(paper.get("references", [])),
        }

        joined_sections = " ".join(section.get("title", "") for section in paper.get("sections", []))
        concept_text = " ".join([title, paper.get("abstract", ""), joined_sections])
        concepts = sorted(set(extract_concepts(concept_text)))
        paper_concepts.append({"paper_id": pid, "paper_title": title, "concepts": concepts})

        for concept in concepts:
            concept_to_papers[concept].add(pid)
        for source, target in combinations(concepts, 2):
            concept_edge_papers[(source, target)].add(pid)

        for ref in paper.get("references", []):
            rid = reference_key(ref)
            if not rid:
                continue
            citation_nodes.setdefault(
                rid,
                {
                    "id": rid,
                    "type": "reference",
                    "title": ref.get("title", "").strip(),
                    "doi": (ref.get("doi") or "").strip(),
                    "first_author": (ref.get("first_author") or "").strip(),
                    "year": (ref.get("year") or "").strip(),
                    "journal": (ref.get("journal") or "").strip(),
                },
            )
            citation_edges.append(
                {
                    "source": pid,
                    "target": rid,
                    "type": "cites",
                }
            )
            reference_cited_by[rid].add(pid)

    citation_network = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stats": {
            "paper_nodes": sum(1 for node in citation_nodes.values() if node["type"] == "paper"),
            "reference_nodes": sum(1 for node in citation_nodes.values() if node["type"] == "reference"),
            "total_nodes": len(citation_nodes),
            "total_edges": len(citation_edges),
        },
        "nodes": sorted(citation_nodes.values(), key=lambda item: (item["type"], item["title"].lower())),
        "edges": citation_edges,
        "reference_cited_by": {
            key: sorted(value) for key, value in sorted(reference_cited_by.items())
        },
    }

    concept_nodes = []
    for concept, papers_for_concept in sorted(concept_to_papers.items()):
        concept_nodes.append(
            {
                "id": concept,
                "label": concept,
                "paper_count": len(papers_for_concept),
                "papers": sorted(papers_for_concept),
            }
        )

    concept_edges = []
    for (source, target), supporting_papers in sorted(concept_edge_papers.items()):
        concept_edges.append(
            {
                "source": source,
                "target": target,
                "weight": len(supporting_papers),
                "papers": sorted(supporting_papers),
            }
        )

    concept_map = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stats": {
            "concept_count": len(concept_nodes),
            "edge_count": len(concept_edges),
            "paper_count": len(papers),
        },
        "concepts": concept_nodes,
        "edges": concept_edges,
        "paper_concepts": paper_concepts,
    }

    citation_path = Path(args.citation_output)
    concept_path = Path(args.concept_output)
    citation_path.parent.mkdir(parents=True, exist_ok=True)
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    citation_path.write_text(json.dumps(citation_network, ensure_ascii=False, indent=2), encoding="utf-8")
    concept_path.write_text(json.dumps(concept_map, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已读论文: {len(papers)}")
    print(
        f"引用图节点/边: {citation_network['stats']['total_nodes']}/{citation_network['stats']['total_edges']}"
    )
    print(
        f"概念图概念/边: {concept_map['stats']['concept_count']}/{concept_map['stats']['edge_count']}"
    )
    print(f"引用图输出: {citation_path}")
    print(f"概念图输出: {concept_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
