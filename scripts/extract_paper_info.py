import argparse
import json
from pathlib import Path

from lxml import etree

from init_pipeline import build_paths


TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


def extract_single_reference(bibl) -> dict:
    ref = {}

    title_el = bibl.find('.//tei:title[@level="a"]', TEI_NS)
    if title_el is None:
        title_el = bibl.find(".//tei:title", TEI_NS)
    ref["title"] = title_el.text.strip() if title_el is not None and title_el.text else ""

    first_author = bibl.find(".//tei:author/tei:persName/tei:surname", TEI_NS)
    ref["first_author"] = first_author.text.strip() if first_author is not None and first_author.text else ""

    date_el = bibl.find('.//tei:date[@type="published"]', TEI_NS)
    if date_el is None:
        date_el = bibl.find(".//tei:date", TEI_NS)
    ref["year"] = date_el.get("when", "")[:4] if date_el is not None else ""

    doi_el = bibl.find('.//tei:idno[@type="DOI"]', TEI_NS)
    ref["doi"] = doi_el.text.strip() if doi_el is not None and doi_el.text else ""

    journal_el = bibl.find('.//tei:title[@level="j"]', TEI_NS)
    ref["journal"] = journal_el.text.strip() if journal_el is not None and journal_el.text else ""

    return ref


def extract_paper_info(tei_path: Path) -> dict:
    tree = etree.parse(str(tei_path))
    root = tree.getroot()

    info = {
        "source_file": tei_path.name,
        "title": "",
        "abstract": "",
        "authors": [],
        "doi": "",
        "sections": [],
        "references": [],
    }

    title_el = root.find(".//tei:titleStmt/tei:title", TEI_NS)
    info["title"] = title_el.text.strip() if title_el is not None and title_el.text else ""

    abstract_el = root.find(".//tei:profileDesc/tei:abstract", TEI_NS)
    if abstract_el is not None:
        info["abstract"] = etree.tostring(
            abstract_el, method="text", encoding="unicode"
        ).strip()

    for author in root.findall(".//tei:fileDesc//tei:author", TEI_NS):
        persname = author.find(".//tei:persName", TEI_NS)
        if persname is None:
            continue
        first = persname.find("tei:forename", TEI_NS)
        last = persname.find("tei:surname", TEI_NS)
        name = f"{first.text if first is not None and first.text else ''} {last.text if last is not None and last.text else ''}".strip()
        if name:
            info["authors"].append(name)

    doi_el = root.find('.//tei:idno[@type="DOI"]', TEI_NS)
    info["doi"] = doi_el.text.strip() if doi_el is not None and doi_el.text else ""

    for div in root.findall(".//tei:body/tei:div", TEI_NS):
        head = div.find("tei:head", TEI_NS)
        section_title = head.text.strip() if head is not None and head.text else "Untitled Section"
        section_text = etree.tostring(div, method="text", encoding="unicode").strip()
        info["sections"].append({"title": section_title, "text": section_text})

    for bibl in root.findall(".//tei:listBibl/tei:biblStruct", TEI_NS):
        ref = extract_single_reference(bibl)
        if ref.get("title"):
            info["references"].append(ref)

    return info


def main() -> int:
    paths = build_paths()

    parser = argparse.ArgumentParser(description="从 GROBID 的 TEI XML 提取结构化论文信息")
    parser.add_argument(
        "--input-dir",
        default=str(paths["parsed_seed"]),
        help="TEI XML 输入目录",
    )
    parser.add_argument(
        "--output-dir",
        default=str(paths["parsed_seed_info"]),
        help="结构化 JSON 输出目录",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tei_files = sorted(input_dir.glob("*.tei.xml"))
    if not tei_files:
        print(f"未找到 TEI XML: {input_dir}")
        return 1

    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print(f"待提取数量: {len(tei_files)}")

    for index, tei_path in enumerate(tei_files, start=1):
        info = extract_paper_info(tei_path)
        output_path = output_dir / f"{tei_path.stem}.json"
        output_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{index}/{len(tei_files)}] 完成: {tei_path.name} -> {output_path.name}")

    print("结构化提取完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
