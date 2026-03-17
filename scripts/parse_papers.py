import argparse
import json
import time
from pathlib import Path

import requests
from requests import RequestException

from grobid_health import inspect_grobid_runtime, summarize_runtime
from init_pipeline import build_paths


def load_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_single_pdf(
    pdf_path: Path,
    output_dir: Path,
    config: dict,
    force: bool,
    timeout: int | None,
) -> tuple[bool, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tei_path = output_dir / f"{pdf_path.stem}.tei.xml"

    if tei_path.exists() and not force:
        return True, f"跳过已存在: {tei_path.name}"

    session = requests.Session()
    session.trust_env = False

    try:
        with pdf_path.open("rb") as pdf_file:
            files = {"input": (pdf_path.name, pdf_file, "application/pdf")}
            data = {
                "consolidateHeader": config.get("consolidate_header", "1"),
                "consolidateCitations": config.get("consolidate_citations", "1"),
                "teiCoordinates": str(config.get("coordinates", False)).lower(),
            }
            response = session.post(
                f"{config['grobid_server'].rstrip('/')}/api/processFulltextDocument",
                files=files,
                data=data,
                timeout=timeout if timeout is not None else config.get("timeout", 60),
            )
    except RequestException as exc:
        return False, f"{pdf_path.name}: 请求失败 ({exc})"
    finally:
        session.close()

    if response.status_code != 200 or not response.text.strip():
        return False, f"{pdf_path.name}: HTTP {response.status_code}"

    tei_path.write_text(response.text, encoding="utf-8")
    return True, f"完成: {pdf_path.name} -> {tei_path.name}"


def parse_all_papers(
    input_dir: Path,
    output_dir: Path,
    config_path: Path,
    force: bool,
    timeout: int | None,
) -> int:
    config = load_config(config_path)
    pdf_files = sorted(input_dir.glob("*.pdf"))

    if not pdf_files:
        print(f"未找到 PDF: {input_dir}")
        return 1

    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print(f"待解析数量: {len(pdf_files)}")

    runtime = inspect_grobid_runtime(config_path)
    if not runtime["ok"]:
        print("GROBID 预检查失败，当前不执行解析。")
        print(summarize_runtime(runtime))
        return 3

    success_count = 0
    failure_count = 0

    for index, pdf_path in enumerate(pdf_files, start=1):
        ok, message = parse_single_pdf(pdf_path, output_dir, config, force, timeout)
        prefix = f"[{index}/{len(pdf_files)}]"
        print(f"{prefix} {message}")
        if ok:
            success_count += 1
        else:
            failure_count += 1
        if index < len(pdf_files):
            time.sleep(config.get("sleep_time", 5))

    print(f"解析完成: 成功 {success_count}，失败 {failure_count}")
    return 0 if failure_count == 0 else 2


def main() -> int:
    paths = build_paths()

    parser = argparse.ArgumentParser(description="调用 GROBID 批量解析论文 PDF")
    parser.add_argument(
        "--input-dir",
        default=str(paths["papers_seed"]),
        help="PDF 输入目录",
    )
    parser.add_argument(
        "--output-dir",
        default=str(paths["parsed_seed"]),
        help="TEI XML 输出目录",
    )
    parser.add_argument(
        "--config",
        default=str(paths["config_json"]),
        help="GROBID 配置文件路径",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的解析结果",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="覆盖 config.json 中的单篇请求超时时间（秒）",
    )
    args = parser.parse_args()

    return parse_all_papers(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output_dir),
        config_path=Path(args.config),
        force=args.force,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
