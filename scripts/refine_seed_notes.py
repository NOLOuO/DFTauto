import argparse
import json
import re
from datetime import date
from pathlib import Path

from init_pipeline import build_paths


KEYWORD_HINTS = [
    ("multi-agent", "多智能体"),
    ("agentic", "Agent 驱动"),
    ("agent", "智能体"),
    ("hierarchical", "层级式架构"),
    ("mcp", "MCP"),
    ("crewai", "CrewAI"),
    ("langgraph", "LangGraph"),
    ("reAct".lower(), "ReAct"),
    ("pydantic", "Pydantic"),
    ("pymatgen", "pymatgen"),
    ("ase", "ASE"),
    ("rdkit", "RDKit"),
    ("architector", "Architector"),
    ("materials project", "Materials Project"),
    ("atomate2", "atomate2"),
    ("atomate", "atomate"),
    ("jobflow", "jobflow"),
    ("aiida", "AiiDA"),
    ("custodian", "Custodian"),
    ("vasp", "VASP"),
    ("quantum espresso", "Quantum ESPRESSO"),
    ("orca", "ORCA"),
    ("cp2k", "CP2K"),
    ("xtb", "xTB"),
    ("slurm", "Slurm"),
    ("hpc", "HPC"),
    ("workflow", "工作流"),
    ("error handling", "错误恢复"),
    ("machine learning potential", "MLP"),
    ("chgnet", "CHGNet"),
    ("m3gnet", "M3GNet"),
    ("mace", "MACE"),
    ("mattersim", "MatterSim"),
]

LLM_HINTS = [
    "DeepSeek-V3-0324",
    "DeepSeek",
    "Claude",
    "GPT-4",
    "GPT-4o",
    "Gemini",
    "Llama",
    "Qwen",
    "Anthropic",
    "OpenAI",
]

BENCHMARK_WORDS = ["benchmark", "dataset", "case study", "evaluation", "experiment", "benchmarking"]
LIMIT_WORDS = ["limitation", "future", "challenge", "roadmap", "discussion"]
ARCH_WORDS = ["architecture", "method", "implementation", "design", "system architecture", "overall structure"]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def split_sentences(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def first_n_sentences(text: str, n: int = 2, max_chars: int = 420) -> str:
    chosen = []
    total = 0
    for sent in split_sentences(text):
        if total + len(sent) > max_chars and chosen:
            break
        chosen.append(sent)
        total += len(sent)
        if len(chosen) >= n:
            break
    return " ".join(chosen)


def find_sections(data: dict, words: list[str]) -> list[dict]:
    result = []
    for section in data.get("sections", []):
        title = clean_text(section.get("title", "")).lower()
        text = clean_text(section.get("text", ""))
        body = f"{title} {text[:2000].lower()}"
        if any(word in body for word in words):
            result.append(section)
    return result


def infer_year(source_name: str) -> str:
    arxiv_match = re.match(r"^(\d{2})(\d{2})\.\d{5}", source_name)
    if arxiv_match:
        return f"20{arxiv_match.group(1)}"
    return "待补充"


def infer_source(source_name: str, doi: str) -> str:
    if re.match(r"^\d{4}\.\d{5}", source_name):
        return f"arXiv:{source_name}"
    if doi:
        return "DOI论文"
    return "待补充"


def infer_summary(title: str, abstract: str) -> str:
    title_lower = title.lower()
    if "review" in title_lower:
        return "综述大模型与自主智能体在化学/材料研究中的应用进展。"
    if "atomate2" in title_lower:
        return "提出面向材料科学任务的模块化工作流基础设施。"
    if "vaspilot" in title_lower:
        return "提出基于 MCP 与多智能体协作的 VASP 自动化平台。"
    if "dreams" in title_lower:
        return "提出面向 DFT 材料模拟的分层多智能体自动化框架。"
    if "el agente" in title_lower:
        return "提出面向量子化学任务的自主智能体系统。"
    if "masgent" in title_lower:
        return "提出将 DFT、MLP 与结构操作统一到自然语言界面的材料模拟智能体。"
    if "tritondft" in title_lower:
        return "提出用于 DFT 自动化的多智能体框架与评测基准。"
    if "augmenting large language models with chemistry tools" in title_lower:
        return "讨论用化学工具增强大模型科研执行能力及安全边界。"
    if "materials design and discovery" in title_lower:
        return "总结 AI 驱动材料设计与发现的主要方法与代表案例。"
    if "llema" in title_lower:
        return "探索将大语言模型与进化搜索结合用于多目标材料发现。"
    return first_n_sentences(abstract, n=1, max_chars=90) or "待补充。"


def extract_hints(text: str) -> list[str]:
    text_lower = text.lower()
    labels = []
    for key, label in KEYWORD_HINTS:
        if key in text_lower and label not in labels:
            labels.append(label)
    return labels


def infer_llm(text: str) -> str:
    for hint in LLM_HINTS:
        if hint.lower() in text.lower():
            return hint
    if "large language model" in text.lower() or "llm" in text.lower():
        return "文中明确使用 LLM，但已提取内容未给出具体型号"
    return "未在已提取内容中明确说明"


def select_refs(references: list[dict]) -> list[dict]:
    scored = []
    for ref in references:
        title = clean_text(ref.get("title", ""))
        if not title:
            continue
        title_lower = title.lower()
        score = sum(2 for key, _ in KEYWORD_HINTS if key in title_lower)
        if "review" in title_lower:
            score += 1
        if ref.get("doi"):
            score += 1
        scored.append((score, title, ref))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:5]]


def find_related_titles(title: str, references: list[dict], all_titles: list[str]) -> list[str]:
    ref_titles = {clean_text(ref.get("title", "")).lower() for ref in references if ref.get("title")}
    related = []
    for other in all_titles:
        if other == title:
            continue
        if clean_text(other).lower() in ref_titles:
            related.append(other)
    return related


def dedupe_lines(lines: list[str]) -> list[str]:
    result = []
    seen = set()
    for line in lines:
        norm = line.strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(line)
    return result


def build_reusable_designs(hints: list[str], title: str) -> list[str]:
    items = []
    if "多智能体" in hints:
        items.append("按角色拆分 Planner / 结构构建 / 计算执行 / 结果校验等子代理，降低单代理上下文负担。")
    if "MCP" in hints:
        items.append("采用协议化工具接口封装结构检索、输入生成、作业提交和结果解析，便于后续替换底层计算后端。")
    if "Slurm" in hints or "HPC" in hints:
        items.append("把 HPC 作业提交、轮询、失败重试独立成工具层，不让上层代理直接拼接集群命令。")
    if "MLP" in hints or "CHGNet" in hints or "M3GNet" in hints or "MACE" in hints:
        items.append("将 MLP 预筛作为高成本 DFT 前的前置阶段，缩短探索回路。")
    if "pymatgen" in hints or "ASE" in hints:
        items.append("结构操作优先走成熟库接口，避免让 LLM 直接生成脆弱的晶体操作细节。")
    if "atomate2" in title.lower():
        items.append("把稳定的计算原语固化成可组合工作流节点，让代理只负责任务编排而不是重复发明流程。")
    return dedupe_lines(items) or ["待在精读后补充可直接复用的设计。"]


def build_limitations(data: dict) -> list[str]:
    sections = find_sections(data, LIMIT_WORDS)
    items = []
    for section in sections[:2]:
        snippet = first_n_sentences(section.get("text", ""), n=2, max_chars=260)
        if snippet:
            items.append(snippet)
    return dedupe_lines(items) or ["待结合 Discussion / Limitations / Conclusion 深读后补充。"]


def build_benchmark_items(data: dict) -> tuple[str, str]:
    sections = find_sections(data, BENCHMARK_WORDS)
    benchmark = first_n_sentences(" ".join(section.get("text", "") for section in sections[:2]), n=2, max_chars=260)
    metrics = first_n_sentences(" ".join(section.get("text", "") for section in sections[:3]), n=2, max_chars=260)
    return benchmark or "待补充", metrics or "待补充"


def build_architecture_text(data: dict, hints: list[str]) -> str:
    sections = find_sections(data, ARCH_WORDS)
    snippets = [first_n_sentences(section.get("text", ""), n=2, max_chars=320) for section in sections[:2]]
    snippets = [s for s in snippets if s]
    if snippets:
        return " ".join(snippets)
    if data.get("abstract"):
        return first_n_sentences(data["abstract"], n=2, max_chars=320)
    return f"待补充。当前可见关键词：{'、'.join(hints) if hints else '暂无明确架构线索'}"


def build_core_problem(data: dict) -> str:
    intro_sections = find_sections(data, ["introduction", "background", "review article"])
    text = " ".join(section.get("text", "") for section in intro_sections[:1]) or data.get("abstract", "")
    return first_n_sentences(text, n=3, max_chars=520) or "待补充。"


def build_key_details(data: dict, hints: list[str]) -> list[str]:
    full_text = " ".join(clean_text(section.get("text", "")) for section in data.get("sections", []))
    text_lower = full_text.lower()
    items = []
    if "hierarchical" in text_lower:
        items.append("Agent 设计模式：文中明确采用层级式任务分解，避免高层代理被实现细节淹没。")
    if "tool" in text_lower or "tool-calling" in text_lower or "mcp" in text_lower:
        items.append("工具接口设计：通过工具调用或协议层暴露结构处理、输入生成、执行与结果解析能力。")
    if "pymatgen" in text_lower or "ase" in text_lower or "rdkit" in text_lower or "architector" in text_lower:
        items.append("结构构建方法：依赖成熟材料/化学库处理结构生成、变换或分子构型。")
    if "input" in text_lower and ("incar" in text_lower or "kpoints" in text_lower or "potcar" in text_lower or "orca" in text_lower):
        items.append("输入文件生成策略：将计算输入拆为模板化、可校验的生成步骤，而不是一次性自由生成。")
    if "error" in text_lower or "troubleshoot" in text_lower or "convergence" in text_lower:
        items.append("错误处理与恢复机制：将错误解析与参数修正纳入显式反馈回路，而不是仅靠一次性执行。")
    if "slurm" in text_lower or "scheduler" in text_lower or "hpc" in text_lower:
        items.append("HPC 作业管理方式：通过 Slurm/HPC 工具层进行提交、轮询与重试。")
    if hints:
        items.append(f"当前可见关键词：{'、'.join(hints)}。")
    return dedupe_lines(items) or ["待结合全文补充关键技术细节。"]


def build_inspiration(hints: list[str], title: str) -> list[str]:
    items = []
    if "多智能体" in hints:
        items.append("可以把本项目的任务拆成规划、结构生成、输入校验、作业执行、错误恢复、结果判读等稳定角色。")
    if "MCP" in hints:
        items.append("适合把本项目的底层能力统一暴露为协议化工具，便于后续接入不同 DFT/建模后端。")
    if "工作流" in hints or "atomate2" in hints:
        items.append("适合把可复用的标准计算流程沉淀成工作流节点，再让代理负责组合与决策。")
    if "MLP" in hints:
        items.append("可将 MLP 预筛与 DFT 精算串成两阶段管线，提高自动化筛选效率。")
    if "review" in title.lower():
        items.append("这篇综述可作为后续分类框架，帮助统一整理 agent、工具、自动化层级与安全边界。")
    return dedupe_lines(items) or ["待精读后补充对本项目设计的具体启发。"]


def build_note(data: dict, all_titles: list[str], round_label: str) -> str:
    source_stem = data["source_file"].replace(".tei.xml", "")
    title = clean_text(data.get("title", "")) or source_stem
    abstract = clean_text(data.get("abstract", ""))
    authors = data.get("authors", [])
    first_author = authors[0] if authors else "待补充"
    year = infer_year(source_stem)
    source = infer_source(source_stem, clean_text(data.get("doi", "")))
    doi = clean_text(data.get("doi", "")) or "待补充"
    summary = infer_summary(title, abstract)
    full_text = " ".join([title, abstract] + [clean_text(section.get("text", "")) for section in data.get("sections", [])])
    hints = extract_hints(full_text)
    llm_info = infer_llm(full_text)
    selected_refs = select_refs(data.get("references", []))
    related_titles = find_related_titles(title, data.get("references", []), all_titles)
    architecture = build_architecture_text(data, hints)
    core_problem = build_core_problem(data)
    benchmark, metrics = build_benchmark_items(data)
    key_details = build_key_details(data, hints)
    reusable_designs = build_reusable_designs(hints, title)
    limitations = build_limitations(data)
    inspirations = build_inspiration(hints, title)

    lines = [
        f"# {title}",
        "",
        "## 元信息",
        f"- **作者**: {first_author} et al., {year}",
        f"- **期刊/来源**: {source}",
        f"- **DOI**: {doi}",
        f"- **精读日期**: {date.today().isoformat()}",
        f"- **来源轮次**: {round_label}",
        "",
        "## 一句话总结",
        summary,
        "",
        "## 核心问题",
        core_problem,
        "",
        "## 技术方案",
        "### 系统架构",
        architecture or "待补充。",
        "",
        "### 关键技术细节",
    ]

    for item in key_details:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "### 使用的 LLM",
            llm_info,
            "",
            "## 实验与验证",
            f"- **Benchmark**: {benchmark}",
            f"- **关键指标**: {metrics}",
            "- **计算开销**: 待结合正文与补充材料补充。",
            "",
            "## 与本项目的关联",
            "### 可直接复用的设计",
        ]
    )
    for item in reusable_designs:
        lines.append(f"- {item}")

    lines.extend(["", "### 本文的局限性"])
    for item in limitations:
        lines.append(f"- {item}")

    lines.extend(["", "### 对我们系统的启发"])
    for item in inspirations:
        lines.append(f"- {item}")

    lines.extend(["", "## 关键参考文献（值得追踪）"])
    if selected_refs:
        for ref in selected_refs:
            ref_title = clean_text(ref.get("title", "")) or "待补充"
            ref_reason = "标题与 Agent / DFT / workflow / 自动化方向高度相关"
            lines.append(f"- {ref_title} — 理由: {ref_reason}")
    else:
        lines.append("- 待补充 — 理由: 参考文献抽取结果需进一步清洗。")

    lines.extend(
        [
            "",
            "## 与其他已读论文的关系",
            "；".join(related_titles) if related_titles else "待结合已读论文交叉补充。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    paths = build_paths()
    parser = argparse.ArgumentParser(description="基于 seed_info 生成更完整的种子论文精读笔记")
    parser.add_argument(
        "--input-dir",
        default=str(paths["parsed_seed_info"]),
        help="结构化 JSON 输入目录",
    )
    parser.add_argument(
        "--output-dir",
        default=str(paths["notes_seed"]),
        help="Markdown 笔记输出目录",
    )
    parser.add_argument(
        "--round-label",
        default="seed",
        help="笔记中的来源轮次标签",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(input_dir.glob("*.json"))
    if not json_files:
        print(f"未找到 JSON: {input_dir}")
        return 1

    all_data = [json.loads(p.read_text(encoding="utf-8")) for p in json_files]
    all_titles = [clean_text(item.get("title", "")) for item in all_data if clean_text(item.get("title", ""))]

    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print(f"待生成数量: {len(all_data)}")

    for index, data in enumerate(all_data, start=1):
        source_stem = data["source_file"].replace(".tei.xml", "")
        output_path = output_dir / f"{source_stem}.md"
        output_path.write_text(build_note(data, all_titles, args.round_label), encoding="utf-8")
        print(f"[{index}/{len(all_data)}] 完成: {output_path.name}")

    print("精读笔记初版生成完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
