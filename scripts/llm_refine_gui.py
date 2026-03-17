from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from grobid_health import inspect_grobid_runtime, summarize_runtime
from init_pipeline import build_paths
from llm_provider_presets import PROVIDER_PRESETS, default_model_id, get_provider, list_models
from llm_refine_notes import build_runtime_config, refine_notes


class RefineApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.repo_root = Path(__file__).resolve().parents[2]
        self.review_root = self.repo_root / "literature-review"
        self.paths = build_paths()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.provider_var = tk.StringVar(value="moonshot")
        self.model_id_var = tk.StringVar(value="kimi2.5")
        self.dataset_var = tk.StringVar(value="seed")
        self.api_key_var = tk.StringVar()
        self.input_dir_var = tk.StringVar(value=str(self.paths["parsed_seed_info"]))
        self.notes_dir_var = tk.StringVar(value=str(self.paths["notes_seed"]))
        self.output_dir_var = tk.StringVar(value=str(self.paths["notes_seed"]))
        self.limit_var = tk.StringVar(value="0")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.max_section_chars_var = tk.StringVar(value="1800")
        self.max_total_section_chars_var = tk.StringVar(value="12000")
        self.max_refs_var = tk.StringVar(value="12")
        self.top_n_var = tk.StringVar(value="10")
        self.parse_timeout_var = tk.StringVar(value="180")
        self.unpaywall_email_var = tk.StringVar()
        self.s2_api_key_var = tk.StringVar()
        self.core_api_key_var = tk.StringVar()
        self.enable_chemrxiv_var = tk.BooleanVar(value=True)
        self.fast_only_var = tk.BooleanVar(value=False)
        self.reset_output_var = tk.BooleanVar(value=True)
        self.auto_refine_var = tk.BooleanVar(value=True)
        self.refine_overwrite_var = tk.BooleanVar(value=False)
        self.grobid_status_var = tk.StringVar(value="GROBID 状态：未检查")

        self.base_url_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.model_note_var = tk.StringVar()
        self.api_env_var = tk.StringVar()

        self._build_ui()
        self._apply_provider_preset()
        self.root.after(200, self._poll_log_queue)
        self.root.after(300, self._check_grobid)

    def _build_ui(self) -> None:
        self.root.title("literature-review 精修工具")
        self.root.geometry("980x760")
        self.root.minsize(880, 680)

        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        basic = ttk.LabelFrame(container, text="模型服务", padding=10)
        basic.pack(fill=tk.X)

        ttk.Label(basic, text="提供方").grid(row=0, column=0, sticky=tk.W, pady=4)
        provider_box = ttk.Combobox(
            basic,
            textvariable=self.provider_var,
            state="readonly",
            values=list(PROVIDER_PRESETS.keys()),
            width=16,
        )
        provider_box.grid(row=0, column=1, sticky=tk.W, pady=4)
        provider_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_provider_preset())

        ttk.Label(basic, text="模型").grid(row=0, column=2, sticky=tk.W, pady=4, padx=(16, 0))
        self.model_box = ttk.Combobox(
            basic,
            textvariable=self.model_id_var,
            state="readonly",
            width=22,
        )
        self.model_box.grid(row=0, column=3, sticky=tk.W, pady=4)
        self.model_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_model_selection())

        ttk.Label(basic, text="API Key").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(basic, textvariable=self.api_key_var, show="*", width=46).grid(
            row=1, column=1, columnspan=3, sticky=tk.EW, pady=4
        )

        ttk.Label(basic, text="Base URL").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(basic, textvariable=self.base_url_var, state="readonly", width=40).grid(
            row=2, column=1, columnspan=3, sticky=tk.EW, pady=4
        )

        ttk.Label(basic, text="模型标识").grid(row=3, column=0, sticky=tk.W, pady=4)
        ttk.Entry(basic, textvariable=self.model_var, state="readonly", width=28).grid(
            row=3, column=1, sticky=tk.W, pady=4
        )
        ttk.Label(basic, text="环境变量名").grid(row=3, column=2, sticky=tk.W, pady=4, padx=(16, 0))
        ttk.Entry(basic, textvariable=self.api_env_var, state="readonly", width=22).grid(
            row=3, column=3, sticky=tk.W, pady=4
        )

        ttk.Label(basic, text="模型说明").grid(row=4, column=0, sticky=tk.W, pady=4)
        ttk.Entry(basic, textvariable=self.model_note_var, state="readonly", width=72).grid(
            row=4, column=1, columnspan=3, sticky=tk.EW, pady=4
        )

        basic.columnconfigure(3, weight=1)

        task = ttk.LabelFrame(container, text="处理范围", padding=10)
        task.pack(fill=tk.X, pady=(12, 0))

        ttk.Label(task, text="任务集").grid(row=0, column=0, sticky=tk.W, pady=4)
        dataset_box = ttk.Combobox(
            task,
            textvariable=self.dataset_var,
            state="readonly",
            values=["seed", "round_1", "round_2", "round_3", "custom"],
            width=16,
        )
        dataset_box.grid(row=0, column=1, sticky=tk.W, pady=4)
        dataset_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_dataset_preset())

        ttk.Label(task, text="限制篇数").grid(row=0, column=2, sticky=tk.W, pady=4, padx=(16, 0))
        ttk.Entry(task, textvariable=self.limit_var, width=10).grid(row=0, column=3, sticky=tk.W, pady=4)

        ttk.Checkbutton(task, text="覆盖已有输出", variable=self.overwrite_var).grid(
            row=0, column=4, sticky=tk.W, pady=4, padx=(16, 0)
        )
        ttk.Checkbutton(task, text="只演练不调用", variable=self.dry_run_var).grid(
            row=0, column=5, sticky=tk.W, pady=4, padx=(16, 0)
        )

        self._dir_row(task, 1, "结构化 JSON 目录", self.input_dir_var)
        self._dir_row(task, 2, "初版笔记目录", self.notes_dir_var)
        self._dir_row(task, 3, "输出目录", self.output_dir_var)

        task.columnconfigure(4, weight=1)

        advanced = ttk.LabelFrame(container, text="高级参数", padding=10)
        advanced.pack(fill=tk.X, pady=(12, 0))

        ttk.Label(advanced, text="单章节最大字符").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(advanced, textvariable=self.max_section_chars_var, width=10).grid(
            row=0, column=1, sticky=tk.W, pady=4
        )
        ttk.Label(advanced, text="总章节最大字符").grid(row=0, column=2, sticky=tk.W, pady=4, padx=(16, 0))
        ttk.Entry(advanced, textvariable=self.max_total_section_chars_var, width=10).grid(
            row=0, column=3, sticky=tk.W, pady=4
        )
        ttk.Label(advanced, text="参考文献上限").grid(row=0, column=4, sticky=tk.W, pady=4, padx=(16, 0))
        ttk.Entry(advanced, textvariable=self.max_refs_var, width=10).grid(row=0, column=5, sticky=tk.W, pady=4)

        pipeline = ttk.LabelFrame(container, text="全流程设置", padding=10)
        pipeline.pack(fill=tk.X, pady=(12, 0))

        ttk.Label(pipeline, textvariable=self.grobid_status_var, foreground="#9c2f00").grid(
            row=0, column=0, columnspan=5, sticky=tk.W, pady=(0, 6)
        )
        ttk.Button(pipeline, text="检查 GROBID", command=self._check_grobid).grid(
            row=0, column=5, sticky=tk.E, pady=(0, 6)
        )

        ttk.Label(pipeline, text="每轮前 N 篇").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(pipeline, textvariable=self.top_n_var, width=10).grid(row=1, column=1, sticky=tk.W, pady=4)
        ttk.Label(pipeline, text="解析超时(秒)").grid(row=1, column=2, sticky=tk.W, pady=4, padx=(16, 0))
        ttk.Entry(pipeline, textvariable=self.parse_timeout_var, width=10).grid(row=1, column=3, sticky=tk.W, pady=4)
        ttk.Label(pipeline, text="Unpaywall 邮箱").grid(row=1, column=4, sticky=tk.W, pady=4, padx=(16, 0))
        ttk.Entry(pipeline, textvariable=self.unpaywall_email_var, width=28).grid(row=1, column=5, sticky=tk.W, pady=4)

        ttk.Label(pipeline, text="Semantic Scholar Key").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(pipeline, textvariable=self.s2_api_key_var, show="*", width=24).grid(
            row=2, column=1, sticky=tk.W, pady=4
        )
        ttk.Label(pipeline, text="CORE Key").grid(row=2, column=2, sticky=tk.W, pady=4, padx=(16, 0))
        ttk.Entry(pipeline, textvariable=self.core_api_key_var, show="*", width=24).grid(
            row=2, column=3, sticky=tk.W, pady=4
        )
        ttk.Checkbutton(pipeline, text="启用 chemRxiv", variable=self.enable_chemrxiv_var).grid(
            row=2, column=4, sticky=tk.W, pady=4, padx=(16, 0)
        )
        ttk.Checkbutton(pipeline, text="仅快速层下载", variable=self.fast_only_var).grid(
            row=2, column=5, sticky=tk.W, pady=4
        )

        ttk.Checkbutton(pipeline, text="运行前清空 output 并重跑", variable=self.reset_output_var).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=4
        )
        ttk.Checkbutton(pipeline, text="每轮结束后自动 LLM 精修", variable=self.auto_refine_var).grid(
            row=3, column=2, columnspan=2, sticky=tk.W, pady=4
        )
        ttk.Checkbutton(pipeline, text="自动精修覆盖已有笔记", variable=self.refine_overwrite_var).grid(
            row=3, column=4, columnspan=2, sticky=tk.W, pady=4
        )

        action = ttk.Frame(container)
        action.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(action, text="载入 seed 预设", command=lambda: self._set_dataset("seed")).pack(side=tk.LEFT)
        ttk.Button(action, text="载入 round_1 预设", command=lambda: self._set_dataset("round_1")).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(action, text="开始精修", command=self._start_refine).pack(side=tk.RIGHT)
        ttk.Button(action, text="一键运行全流程", command=self._start_full_pipeline).pack(side=tk.RIGHT, padx=(0, 8))

        log_frame = ttk.LabelFrame(container, text="运行日志", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.log_widget = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=22, font=("Consolas", 10))
        self.log_widget.pack(fill=tk.BOTH, expand=True)

    def _dir_row(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=4)
        ttk.Entry(parent, textvariable=variable, width=84).grid(row=row, column=1, columnspan=4, sticky=tk.EW, pady=4)
        ttk.Button(parent, text="浏览", command=lambda v=variable: self._browse_dir(v)).grid(
            row=row, column=5, sticky=tk.W, pady=4, padx=(8, 0)
        )

    def _browse_dir(self, variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory(initialdir=variable.get() or str(self.review_root))
        if selected:
            variable.set(selected)
            self.dataset_var.set("custom")

    def _apply_provider_preset(self) -> None:
        provider = get_provider(self.provider_var.get())
        self.base_url_var.set(provider["base_url"])
        self.api_env_var.set(provider["api_key_env"])
        model_items = list_models(self.provider_var.get())
        self.model_box["values"] = [item["id"] for item in model_items]
        self.model_id_var.set(default_model_id(self.provider_var.get()))
        self._apply_model_selection()
        self._log(
            f"已切换到 {provider['label']}，将使用 {provider['base_url']}。"
        )

    def _apply_model_selection(self) -> None:
        provider = get_provider(self.provider_var.get())
        models = {item["id"]: item for item in provider.get("models", [])}
        model_id = self.model_id_var.get().strip()
        selected = models.get(model_id)
        if selected is None and models:
            selected = next(iter(models.values()))
            self.model_id_var.set(selected["id"])
        if selected is None:
            self.model_var.set("")
            self.model_note_var.set("")
            return
        self.model_var.set(selected["api_model"])
        self.model_note_var.set(selected.get("note", ""))

    def _apply_dataset_preset(self) -> None:
        dataset = self.dataset_var.get()
        if dataset == "seed":
            self.input_dir_var.set(str(self.paths["parsed_seed_info"]))
            self.notes_dir_var.set(str(self.paths["notes_seed"]))
            self.output_dir_var.set(str(self.paths["notes_seed"]))
        elif dataset == "round_1":
            self.input_dir_var.set(str(self.paths["parsed_round_1_info"]))
            self.notes_dir_var.set(str(self.paths["notes_round_1"]))
            self.output_dir_var.set(str(self.paths["notes_round_1"]))
        elif dataset == "round_2":
            self.input_dir_var.set(str(self.paths["parsed_round_2_info"]))
            self.notes_dir_var.set(str(self.paths["notes_round_2"]))
            self.output_dir_var.set(str(self.paths["notes_round_2"]))
        elif dataset == "round_3":
            self.input_dir_var.set(str(self.paths["parsed_round_3_info"]))
            self.notes_dir_var.set(str(self.paths["notes_round_3"]))
            self.output_dir_var.set(str(self.paths["notes_round_3"]))

    def _set_dataset(self, dataset: str) -> None:
        self.dataset_var.set(dataset)
        self._apply_dataset_preset()

    def _log(self, message: str) -> None:
        self.log_widget.insert(tk.END, message + "\n")
        self.log_widget.see(tk.END)

    def _poll_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._log(message)
        self.root.after(200, self._poll_log_queue)

    def _validate_int(self, value: str, field_name: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是整数") from exc
        if number < 0:
            raise ValueError(f"{field_name} 不能为负数")
        return number

    def _check_grobid(self) -> bool:
        status = inspect_grobid_runtime(self.paths["config_json"])
        if status["ok"]:
            self.grobid_status_var.set(f"GROBID 状态：正常 | {status['grobid_server']}")
            self._log(f"GROBID 检查通过：{status['grobid_server']}")
            return True

        summary = summarize_runtime(status)
        lines = summary.splitlines()
        short_text = lines[1] if len(lines) > 1 else "GROBID 不可用"
        self.grobid_status_var.set(f"GROBID 状态：异常 | {short_text}")
        self._log(summary)
        return False

    def _start_refine(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("提示", "当前已有任务在运行，请等待完成。")
            return

        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showerror("缺少 API Key", "请先填写 API Key。")
            return

        try:
            limit = self._validate_int(self.limit_var.get().strip() or "0", "限制篇数")
            max_section_chars = self._validate_int(self.max_section_chars_var.get().strip(), "单章节最大字符")
            max_total_section_chars = self._validate_int(
                self.max_total_section_chars_var.get().strip(), "总章节最大字符"
            )
            max_refs = self._validate_int(self.max_refs_var.get().strip(), "参考文献上限")
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        provider = self.provider_var.get()
        model_id = self.model_id_var.get().strip()
        input_dir = Path(self.input_dir_var.get().strip())
        notes_dir = Path(self.notes_dir_var.get().strip())
        output_dir = Path(self.output_dir_var.get().strip())
        log_path = self.paths["llm_refine_log_jsonl"]

        if not input_dir.exists():
            messagebox.showerror("路径错误", f"输入目录不存在：{input_dir}")
            return
        if not notes_dir.exists():
            messagebox.showerror("路径错误", f"初版笔记目录不存在：{notes_dir}")
            return

        self._log("=" * 72)
        self._log(f"开始任务：provider={provider}, model={model_id}, input={input_dir}")

        def logger(message: str) -> None:
            self.log_queue.put(message)

        def worker() -> None:
            try:
                config = build_runtime_config(provider=provider, model_id=model_id, api_key_value=api_key)
                if config.get("supports_temperature") and "temperature" not in config:
                    config["temperature"] = config.get("default_temperature")
                if config.get("supports_top_p") and "top_p" not in config:
                    config["top_p"] = config.get("default_top_p")
                exit_code = refine_notes(
                    input_dir=input_dir,
                    notes_dir=notes_dir,
                    output_dir=output_dir,
                    config=config,
                    limit=limit,
                    max_section_chars=max_section_chars,
                    max_total_section_chars=max_total_section_chars,
                    max_refs=max_refs,
                    overwrite=self.overwrite_var.get(),
                    dry_run=self.dry_run_var.get(),
                    log_path=log_path,
                    logger=logger,
                )
                if exit_code == 0:
                    self.log_queue.put("任务结束：全部处理成功。")
                elif exit_code == 2:
                    self.log_queue.put("任务结束：已完成部分处理，但存在失败条目。")
                else:
                    self.log_queue.put(f"任务结束：exit_code={exit_code}")
            except Exception as exc:  # noqa: BLE001
                self.log_queue.put(f"任务异常终止：{exc}")

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _start_full_pipeline(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("提示", "当前已有任务在运行，请等待完成。")
            return

        if not self._check_grobid():
            messagebox.showerror(
                "GROBID 不可用",
                "当前 GROBID 未就绪，详细诊断信息已写入下方日志。\n请先修复 Docker / GROBID，再启动全流程。",
            )
            return

        try:
            top_n = self._validate_int(self.top_n_var.get().strip(), "每轮前 N 篇")
            parse_timeout = self._validate_int(self.parse_timeout_var.get().strip(), "解析超时")
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        provider = self.provider_var.get()
        model_id = self.model_id_var.get().strip()
        api_key = self.api_key_var.get().strip()

        env = os.environ.copy()
        api_env_name = self.api_env_var.get().strip()
        if self.auto_refine_var.get():
            if not api_key:
                messagebox.showerror("缺少 API Key", "开启自动精修时，必须填写 API Key。")
                return
            env[api_env_name] = api_key

        cmd = [
            sys.executable,
            str(self.review_root / "scripts" / "run_three_rounds.py"),
            "--top-n",
            str(top_n),
            "--parse-timeout",
            str(parse_timeout),
        ]
        if self.unpaywall_email_var.get().strip():
            cmd.extend(["--unpaywall-email", self.unpaywall_email_var.get().strip()])
        if self.s2_api_key_var.get().strip():
            cmd.extend(["--s2-api-key", self.s2_api_key_var.get().strip()])
        if self.core_api_key_var.get().strip():
            cmd.extend(["--core-api-key", self.core_api_key_var.get().strip()])
        if not self.enable_chemrxiv_var.get():
            cmd.append("--no-enable-chemrxiv")
        if self.fast_only_var.get():
            cmd.append("--fast-only")
        if self.reset_output_var.get():
            cmd.append("--reset-output")
        if self.auto_refine_var.get():
            cmd.extend(["--auto-refine", "--llm-provider", provider, "--llm-model-id", model_id])
            if self.refine_overwrite_var.get():
                cmd.append("--refine-overwrite")

        self._log("=" * 72)
        self._log(f"启动全流程：provider={provider}, model={model_id}, top_n={top_n}")

        def worker() -> None:
            try:
                process = subprocess.Popen(
                    cmd,
                    cwd=str(self.review_root),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                assert process.stdout is not None
                for line in process.stdout:
                    self.log_queue.put(line.rstrip())
                code = process.wait()
                if code == 0:
                    self.log_queue.put("全流程运行结束：成功。")
                else:
                    self.log_queue.put(f"全流程运行结束：exit_code={code}")
            except Exception as exc:  # noqa: BLE001
                self.log_queue.put(f"全流程运行异常终止：{exc}")

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()


def main() -> int:
    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    RefineApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
