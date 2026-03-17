from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from grobid_health import inspect_grobid_runtime, summarize_runtime
from init_pipeline import ROUND_LABELS, build_paths, count_files, existing_input_dirs, review_root
from llm_refine_notes import build_runtime_config, refine_notes
from reset_output import reset_output


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def python_cmd(script_name: str, *args: str | Path) -> list[str | Path]:
    return [sys.executable, review_root() / "scripts" / script_name, *args]


def run_cmd(cmd: list[str | Path], dry_run: bool) -> int:
    line = " ".join(str(part) for part in cmd)
    print(f"  $ {line}")
    if dry_run:
        print("  [dry-run]")
        return 0
    result = subprocess.run([str(part) for part in cmd], cwd=review_root())
    return int(result.returncode)


def print_banner(title: str) -> None:
    print("\n" + "━" * 72)
    print(title)
    print("━" * 72)


def round_input_dirs(paths: dict[str, Path], round_index: int) -> list[Path]:
    rounds = ROUND_LABELS[: max(0, round_index - 1)]
    return existing_input_dirs(paths, rounds=rounds)


def maybe_auto_refine(
    *,
    round_label: str,
    input_dir: Path,
    notes_dir: Path,
    config: dict,
    overwrite: bool,
    dry_run: bool,
    log_path: Path,
) -> int:
    if count_files(input_dir, "*.json") == 0:
        print(f"  {round_label} 没有结构化 JSON，跳过自动精修")
        return 0
    print(f"  自动精修: {round_label}")
    return refine_notes(
        input_dir=input_dir,
        notes_dir=notes_dir,
        output_dir=notes_dir,
        config=config,
        overwrite=overwrite,
        dry_run=dry_run,
        log_path=log_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="重新执行 literature-review 的 3-round 文献流程")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不实际执行")
    parser.add_argument("--top-n", type=int, default=10, help="每个 round 的前 N 篇候选")
    parser.add_argument("--unpaywall-email", default="", help="可选的 Unpaywall 邮箱")
    parser.add_argument("--s2-api-key", default=os.environ.get("S2_API_KEY", ""), help="可选的 Semantic Scholar API Key")
    parser.add_argument("--core-api-key", default=os.environ.get("CORE_API_KEY", ""), help="可选的 CORE API Key")
    parser.add_argument(
        "--enable-chemrxiv",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否启用 chemRxiv 渠道",
    )
    parser.add_argument("--fast-only", action="store_true", help="只跑快速层下载渠道")
    parser.add_argument("--parse-timeout", type=int, default=180, help="GROBID 单篇超时秒数")
    parser.add_argument("--reset-output", action="store_true", help="执行前先清空 output 并重建目录")
    parser.add_argument("--auto-refine", action="store_true", help="每个阶段生成初版笔记后自动调用 LLM 精修")
    parser.add_argument("--llm-provider", default="moonshot", help="自动精修时使用的 provider")
    parser.add_argument("--llm-model-id", default="", help="自动精修时使用的 model id")
    parser.add_argument("--refine-overwrite", action="store_true", help="自动精修时覆盖已有笔记")
    args = parser.parse_args()

    paths = build_paths()
    llm_config = None

    if args.reset_output:
        print_banner("预处理 — 清空 output 并重建目录")
        reset_output()
        print("output 已重置")
        paths = build_paths()

    if args.auto_refine:
        llm_config = build_runtime_config(
            provider=args.llm_provider,
            model_id=args.llm_model_id or None,
        )
        if llm_config.get("supports_temperature") and "temperature" not in llm_config:
            llm_config["temperature"] = llm_config.get("default_temperature")
        if llm_config.get("supports_top_p") and "top_p" not in llm_config:
            llm_config["top_p"] = llm_config.get("default_top_p")

    runtime = inspect_grobid_runtime(paths["config_json"])
    if not runtime["ok"]:
        print_banner("启动前检查 — GROBID 不可用")
        print(summarize_runtime(runtime))
        print("中止：请先修复 GROBID 或 Docker，再重新运行全流程。")
        return 3

    print_banner("Phase 1 — 重新解析 seed 论文")
    phase1_cmds = [
        python_cmd("parse_papers.py", "--input-dir", paths["papers_seed"], "--output-dir", paths["parsed_seed"], "--timeout", str(args.parse_timeout)),
        python_cmd("extract_paper_info.py", "--input-dir", paths["parsed_seed"], "--output-dir", paths["parsed_seed_info"]),
        python_cmd("refine_seed_notes.py", "--input-dir", paths["parsed_seed_info"], "--output-dir", paths["notes_seed"], "--round-label", "seed"),
    ]
    for cmd in phase1_cmds:
        code = run_cmd(cmd, args.dry_run)
        if code != 0:
            print(f"中止：seed 阶段命令失败，exit_code={code}")
            return code
    if args.auto_refine and llm_config is not None:
        code = maybe_auto_refine(
            round_label="seed",
            input_dir=paths["parsed_seed_info"],
            notes_dir=paths["notes_seed"],
            config=llm_config,
            overwrite=args.refine_overwrite,
            dry_run=args.dry_run,
            log_path=paths["llm_refine_log_jsonl"],
        )
        if code not in {0, 2}:
            print(f"中止：seed 自动精修失败，exit_code={code}")
            return code

    for round_index, round_label in enumerate(ROUND_LABELS, start=1):
        print_banner(f"{round_label} — 候选提取、下载、解析")
        input_dirs = round_input_dirs(paths, round_index)
        queue_csv = paths[f"{round_label}_queue_csv"]
        download_log = paths[f"{round_label}_download_log_csv"]
        summary_md = paths[f"{round_label}_download_summary_md"]
        summary_json = paths[f"{round_label}_download_summary_json"]

        cmds = [
            python_cmd("extract_refs.py", "--input-dirs", *input_dirs),
            python_cmd(
                "score_refs.py",
                "--input-dirs",
                *input_dirs,
                "--output-csv",
                queue_csv,
                "--download-log",
                download_log,
                "--top-n",
                str(args.top_n),
                "--target-round",
                round_label,
            ),
            python_cmd(
                "download_papers.py",
                "--queue-csv",
                queue_csv,
                "--output-dir",
                paths[f"papers_{round_label}"],
                "--download-log",
                download_log,
                "--top-n",
                str(args.top_n),
                "--target-round",
                round_label,
                "--summary-md",
                summary_md,
                "--summary-json",
                summary_json,
            ),
        ]
        if args.unpaywall_email:
            cmds[2].extend(["--unpaywall-email", args.unpaywall_email])
        if args.s2_api_key:
            cmds[2].extend(["--s2-api-key", args.s2_api_key])
        if args.core_api_key:
            cmds[2].extend(["--core-api-key", args.core_api_key])
        if not args.enable_chemrxiv:
            cmds[2].append("--no-enable-chemrxiv")
        if args.fast_only:
            cmds[2].append("--fast-only")

        for cmd in cmds:
            code = run_cmd(cmd, args.dry_run)
            if code != 0:
                print(f"中止：{round_label} 阶段命令失败，exit_code={code}")
                return code

        paper_dir = paths[f"papers_{round_label}"]
        if count_files(paper_dir, "*.pdf") > 0:
            parse_cmds = [
                python_cmd("parse_papers.py", "--input-dir", paper_dir, "--output-dir", paths[f"parsed_{round_label}"], "--timeout", str(args.parse_timeout)),
                python_cmd("extract_paper_info.py", "--input-dir", paths[f"parsed_{round_label}"], "--output-dir", paths[f"parsed_{round_label}_info"]),
                python_cmd("refine_seed_notes.py", "--input-dir", paths[f"parsed_{round_label}_info"], "--output-dir", paths[f"notes_{round_label}"], "--round-label", round_label),
            ]
            for cmd in parse_cmds:
                code = run_cmd(cmd, args.dry_run)
                if code != 0:
                    print(f"中止：{round_label} 解析阶段命令失败，exit_code={code}")
                    return code
            if args.auto_refine and llm_config is not None:
                code = maybe_auto_refine(
                    round_label=round_label,
                    input_dir=paths[f"parsed_{round_label}_info"],
                    notes_dir=paths[f"notes_{round_label}"],
                    config=llm_config,
                    overwrite=args.refine_overwrite,
                    dry_run=args.dry_run,
                    log_path=paths["llm_refine_log_jsonl"],
                )
                if code not in {0, 2}:
                    print(f"中止：{round_label} 自动精修失败，exit_code={code}")
                    return code
        else:
            print(f"  {round_label} 没有成功下载的 PDF，跳过解析阶段")

        updated_input_dirs = existing_input_dirs(paths, rounds=ROUND_LABELS[:round_index])
        for cmd in [
            python_cmd("build_graph.py", "--input-dirs", *updated_input_dirs),
            python_cmd("generate_report.py", "--input-dirs", *updated_input_dirs),
        ]:
            code = run_cmd(cmd, args.dry_run)
            if code != 0:
                print(f"中止：{round_label} 汇总阶段命令失败，exit_code={code}")
                return code

    print_banner("全部 round 执行完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
