# literature-review

`literature-review` 是一个可直接运行的文献调研程序，目标是从一组种子论文出发，自动完成：

- PDF 解析
- 结构化信息抽取
- 初版中文笔记生成
- 参考文献评分
- 分轮扩展下载
- 每轮下载记录输出
- 最终综述与知识空白整理
- 可选的大模型自动精修

这个目录现在按“**可交付 / 可发布程序**”方式整理，外部用户只需要关心：

- `input/`
- `output/`
- 启动入口

## 目的

本程序面向 **DFT 自动化 / Agent for Science** 的文献调研任务，目标是把一批种子论文逐步扩展成结构化知识库，而不是仅仅把 PDF 存在本地。

最终产出包括：

- `output/notes/` 中的中文笔记
- `output/records/round_logs/` 中每轮下载记录
- `output/records/graph/` 中的引用图与概念图
- `output/reports/` 中的综述、知识空白和汇总表

## 原理

程序的原理是“**结构化解析 + 滚雪球扩展 + 分轮留痕**”。

执行逻辑如下：

1. 从 `input/papers/seed/` 读取种子论文 PDF
2. 用 GROBID 把 PDF 转成 TEI XML
3. 从 TEI XML 中提取标题、摘要、章节、作者、参考文献
4. 生成结构化 JSON 和初版中文笔记
5. 对参考文献做启发式评分
6. 每轮取前 `10` 篇候选
7. 通过开放获取渠道下载
8. 下载失败则跳过，但保留失败记录
9. 连续运行 `round_1 -> round_2 -> round_3`
10. 每轮结束后更新图谱和报告
11. 如果开启自动精修，则用大模型对笔记做升级

## 方法

### 基础方法

```text
input/papers/seed/*.pdf
  -> output/parsed/seed/*.tei.xml
  -> output/parsed/seed_info/*.json
  -> output/notes/seed/*.md
  -> output/records/queue/*.csv
  -> output/downloads/round_1/*.pdf
  -> output/parsed/round_1/*.tei.xml
  -> ...
  -> output/reports/*
```

### 三轮规则

- 共运行 `3` 个 round
- 每轮固定取当前候选中的前 `10` 篇
- 下载不到直接跳过，不阻塞流程
- 每轮输出：
  - 候选队列 CSV
  - 下载日志 CSV
  - 下载摘要 Markdown
  - 下载摘要 JSON

### 下载方法

当前下载策略仍然只走合法开放获取渠道，但已经扩展为“两层下载”：

快速层：

1. `arXiv`
2. `Semantic Scholar`
3. `Unpaywall`
4. `Crossref`

深度层：

5. `CORE`
6. `PMC / Europe PMC`
7. `OpenAlex`
8. `chemRxiv`

规则如下：

- 快速层任一渠道下载成功即停止
- 快速层全部失败才进入深度层
- 下载到的文件必须通过 PDF 头校验
- 某个渠道失败不会中断整轮流程
- 所有渠道都失败时记为 `manual_download_needed`
- 每轮摘要会统计各渠道命中次数

## 目录结构

```text
literature-review/
├── input/
│   ├── papers/
│   │   └── seed/                    # 用户提供的种子论文 PDF
│   └── notes/                       # 可选的用户输入 Markdown
├── output/
│   ├── downloads/
│   │   ├── round_1/
│   │   ├── round_2/
│   │   └── round_3/
│   ├── parsed/
│   │   ├── seed/
│   │   ├── seed_info/
│   │   ├── round_1/
│   │   ├── round_1_info/
│   │   ├── round_2/
│   │   ├── round_2_info/
│   │   ├── round_3/
│   │   └── round_3_info/
│   ├── notes/
│   │   ├── seed/
│   │   ├── round_1/
│   │   ├── round_2/
│   │   └── round_3/
│   ├── records/
│   │   ├── graph/
│   │   │   ├── citation_network.json
│   │   │   └── concept_map.json
│   │   ├── queue/
│   │   │   ├── references_catalog.json
│   │   │   ├── references_catalog.csv
│   │   │   ├── reference_mentions.csv
│   │   │   ├── round_1_reading_queue.csv
│   │   │   ├── round_1_download_log.csv
│   │   │   ├── round_2_reading_queue.csv
│   │   │   ├── round_2_download_log.csv
│   │   │   ├── round_3_reading_queue.csv
│   │   │   └── round_3_download_log.csv
│   │   ├── round_logs/
│   │   │   ├── round_1_download_summary.md
│   │   │   ├── round_1_download_summary.json
│   │   │   ├── round_2_download_summary.md
│   │   │   ├── round_2_download_summary.json
│   │   │   ├── round_3_download_summary.md
│   │   │   └── round_3_download_summary.json
│   │   └── llm_refine_log.jsonl
│   └── reports/
│       ├── literature_review.xlsx
│       ├── review_summary.md
│       └── knowledge_gaps.md
├── scripts/
├── config.json
├── launch_gui.py
├── launch_gui.bat
└── README.md
```

## 一键运行

### 图形界面

启动方式：

```powershell
D:/miniconda3/Scripts/activate
conda activate p312env
python literature-review/launch_gui.py
```

或者直接双击：

```text
literature-review/launch_gui.bat
```

### 运行方式

在 GUI 中设置好：

- `provider`
- `model`
- API Key
- 每轮前 `N` 篇
- GROBID 解析超时
- 可选的 Unpaywall 邮箱
- 可选的 `Semantic Scholar API Key`
- 可选的 `CORE API Key`
- 是否启用 `chemRxiv`
- 是否只跑快速层
- 是否自动 LLM 精修

然后点击：

`一键运行全流程`

点击后程序会自动：

1. 清空并重建 `output/`
2. 重新解析 `seed`
3. 自动跑完 `round_1`、`round_2`、`round_3`
4. 每轮生成下载记录
5. 可选地自动做 LLM 精修

也就是说，参数设置完成后，用户只需要等待程序跑完，不需要再手动参与中间步骤。

程序现在会在真正开始前先做一次 `GROBID` 健康检查：

- 如果 `config.json` 里的 `grobid_server` 不可访问，会直接提前中止
- GUI 会显示当前 `GROBID` 状态，并提供“检查 GROBID”按钮
- 命令行会打印更具体的 Docker / WSL / GROBID 诊断信息

## 命令行入口

如果不走 GUI，可以直接运行总控：

```powershell
D:/miniconda3/Scripts/activate
conda activate p312env
python literature-review/scripts/run_three_rounds.py --top-n 10 --reset-output
```

如果要启用增强下载渠道配置：

```powershell
python literature-review/scripts/run_three_rounds.py `
  --top-n 10 `
  --reset-output `
  --unpaywall-email your_mail@example.com `
  --s2-api-key your_semantic_scholar_key `
  --core-api-key your_core_key
```

如果你不填写：

- `Semantic Scholar API Key`：程序仍会调用 Semantic Scholar，只是走默认限速
- `CORE API Key`：程序会自动跳过 CORE 渠道

如果要启用自动 LLM 精修：

```powershell
python literature-review/scripts/run_three_rounds.py `
  --top-n 10 `
  --reset-output `
  --auto-refine `
  --llm-provider moonshot `
  --llm-model-id kimi2.5
```

## 大模型接口

当前只保留国内服务，并强制 provider 与 model 对应：

- `moonshot`
  - `kimi2.5` -> `kimi-k2.5`
- `deepseek`
  - `deepseek-reasoner`
- `minimax`
  - `minimax2.5` -> `MiniMax-M2.5`
  - `minimax2.1` -> `MiniMax-M2.1`
- `bailian`
  - `qwen3.5-plus` -> 当前兼容模式使用 `qwen-plus`

程序会校验服务地址，只允许国内服务地址。

## 核心脚本

- [run_three_rounds.py](F:\DFTauto\literature-review\scripts\run_three_rounds.py)
  3-round 总控脚本

- [reset_output.py](F:\DFTauto\literature-review\scripts\reset_output.py)
  清空并重建 `output/`

- [parse_papers.py](F:\DFTauto\literature-review\scripts\parse_papers.py)
  PDF -> TEI XML

- [extract_paper_info.py](F:\DFTauto\literature-review\scripts\extract_paper_info.py)
  TEI XML -> JSON

- [refine_seed_notes.py](F:\DFTauto\literature-review\scripts\refine_seed_notes.py)
  JSON -> 初版笔记

- [score_refs.py](F:\DFTauto\literature-review\scripts\score_refs.py)
  参考文献评分

- [download_papers.py](F:\DFTauto\literature-review\scripts\download_papers.py)
  两层开放获取下载、PDF 校验、每轮记录与渠道统计

- [llm_refine_gui.py](F:\DFTauto\literature-review\scripts\llm_refine_gui.py)
  图形界面

- [llm_refine_notes.py](F:\DFTauto\literature-review\scripts\llm_refine_notes.py)
  LLM 自动精修

## 当前边界

- 评分仍然是启发式方法，不是完整的学术检索排序系统。
- 下载只走开放获取渠道，不会绕过版权限制。
- `CORE` 需要用户自行提供 API Key；未提供时会自动跳过。
- `Semantic Scholar` 即使不提供 key 也能运行，但速率更低。
- GROBID 服务如果没有启动，解析阶段会失败。
- 当前这台机器上已经定位到的现状是：`Docker Desktop` 已安装，但 `Docker Linux Engine` 长时间停在 `starting`，所以本地 `GROBID` 还没有真正起来。
- 初版笔记是自动草稿，不等于人工深度精读。
- LLM 精修质量受模型能力和上下文长度影响。

## 相关文件

- 任务规范：[LITERATURE_RESEARCH_AGENT.md](F:\DFTauto\LITERATURE_RESEARCH_AGENT.md)
- 仓库约束：[AGENTS.md](F:\DFTauto\AGENTS.md)
