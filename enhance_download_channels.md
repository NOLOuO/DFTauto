# 增强文献下载渠道 — 实现规格

> **目标**：在 `download_papers.py` 中扩展开放获取下载源，将命中率最大化，同时保持"只走合法 OA 渠道"的原则不变。

---

## 1. 新架构：两层下载策略

将所有渠道分为**快速层**和**深度层**，串行执行。快速层命中即停，全部失败才进入深度层。

```
快速层（低延迟、高命中）        深度层（稍慢、补漏）
┌──────────────────────┐     ┌──────────────────────┐
│ 1. arXiv             │     │ 5. CORE              │
│ 2. Semantic Scholar   │     │ 6. PMC / Europe PMC  │
│ 3. Unpaywall         │     │ 7. OpenAlex           │
│ 4. Crossref link     │     │ 8. chemRxiv (可选)    │
└──────────────────────┘     └──────────────────────┘
         ↓ 全部失败                    ↓ 全部失败
                    manual_download_needed
```

**核心逻辑**：任何一层中某个渠道成功下载到 PDF，立即跳出整个流程，标记为 `downloaded`。

---

## 2. 各渠道实现细节

### 2.1 arXiv（已有，保持不变）

- 用论文标题搜索 arXiv API
- 标题相似度足够高则下载 PDF
- 无需认证

### 2.2 Semantic Scholar API（新增 — 快速层）

- **端点**：`https://api.semanticscholar.org/graph/v1/paper/`
- **查询方式**：
  - 有 DOI：`GET /graph/v1/paper/DOI:{doi}?fields=openAccessPdf`
  - 无 DOI：`GET /graph/v1/paper/search?query={title}&fields=openAccessPdf&limit=1`
- **提取字段**：`openAccessPdf.url`
- **认证**：无需 API key 即可用（速率 100 req/5min）。如用户提供 `--s2-api-key`，在 header 加 `x-api-key`（速率提升到 1 req/sec 持续）
- **命令行参数**：`--s2-api-key`（可选）
- **注意事项**：
  - 返回的 URL 可能是直接 PDF 链接，也可能是 landing page，下载后需检查 Content-Type 是否为 `application/pdf`
  - 标题搜索时对第一条结果做标题相似度校验（复用 arXiv 的相似度逻辑）

### 2.3 Unpaywall（已有，保持不变）

- 需要 DOI + `--unpaywall-email`
- 查询 `https://api.unpaywall.org/v2/{doi}?email={email}`
- 提取 `best_oa_location.url_for_pdf`

### 2.4 Crossref link（新增 — 快速层）

- **前提**：论文有 DOI
- **端点**：`GET https://api.crossref.org/works/{doi}`
- **认证**：无需。建议在 header 加 `mailto:{email}`（polite pool，速率更高）
- **提取逻辑**：
  1. 检查 `message.license` 数组，看是否有 Creative Commons 或其他 OA 许可
  2. 检查 `message.link` 数组，找 `content-type: "application/pdf"` 且 `intended-application: "text-mining"` 或 `"similarity-checking"` 的条目
  3. 如果 license 是 CC 且有 PDF link，尝试下载
- **命令行参数**：复用 `--unpaywall-email`（用于 Crossref polite pool 的 mailto）
- **注意事项**：
  - 不是所有 Crossref 记录都有 link 字段
  - 有些 link 需要跟随重定向才能拿到实际 PDF
  - 设置合理的 timeout（10s）

### 2.5 CORE（新增 — 深度层）

- **端点**：`https://api.core.ac.uk/v3/search/works`
- **查询方式**：
  - 有 DOI：`GET /v3/search/works?q=doi:"{doi}"&limit=1`
  - 无 DOI：`GET /v3/search/works?q=title:"{title}"&limit=1`
- **提取字段**：`results[0].downloadUrl` 或 `results[0].links` 中的 PDF 链接
- **认证**：**必需** API key。免费申请：https://core.ac.uk/services/api
- **命令行参数**：`--core-api-key`（可选，不提供则跳过此渠道）
- **Header**：`Authorization: Bearer {api_key}`
- **注意事项**：
  - CORE 的 downloadUrl 有时返回 HTML 页面而非 PDF，需验证 Content-Type
  - 速率限制：免费 key 约 10 req/sec

### 2.6 PMC / Europe PMC（已有，移至深度层）

- 用 DOI 或标题搜索 Europe PMC
- 如果找到 PMCID，下载 PMC PDF
- 无需认证

### 2.7 OpenAlex（新增 — 深度层）

- **端点**：`https://api.openalex.org/works`
- **查询方式**：
  - 有 DOI：`GET /works/doi:{doi}`
  - 无 DOI：`GET /works?filter=title.search:{title}&per_page=1`
- **提取字段**：`open_access.oa_url`
- **认证**：完全免费，无需 key。建议在查询参数加 `mailto={email}` 进入 polite pool
- **命令行参数**：复用 `--unpaywall-email`
- **注意事项**：
  - `oa_url` 可能是 landing page 而非直接 PDF，需检查
  - 如果 `open_access.is_oa` 为 false 则直接跳过，不必尝试下载

### 2.8 chemRxiv（新增 — 深度层，可选）

- **端点**：chemRxiv 使用 Figshare API
  - `GET https://chemrxiv.org/engage/chemrxiv/public-api/v1/items?searchTerm={title}`
- **提取字段**：从搜索结果中找匹配文章，提取 `asset.original.url`（PDF 下载链接）
- **认证**：无需
- **启用条件**：可以用 `--enable-chemrxiv` 开关控制，默认开启（因为用户做计算化学）
- **注意事项**：
  - 标题搜索需做相似度校验
  - chemRxiv API 文档可能变动，需做好异常处理

---

## 3. 命令行参数变更

在现有参数基础上新增：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--s2-api-key` | str | None | Semantic Scholar API key（可选，提高速率） |
| `--core-api-key` | str | None | CORE API key（不提供则跳过 CORE 渠道） |
| `--enable-chemrxiv` | flag | True | 启用 chemRxiv 搜索 |
| `--fast-only` | flag | False | 只运行快速层，跳过深度层 |

已有参数保持不变：
- `--unpaywall-email`：同时复用于 Crossref polite pool 和 OpenAlex polite pool

---

## 4. 代码结构建议

### 4.1 下载函数组织

```python
# 每个渠道一个独立函数，统一签名：
# def try_download_from_xxx(paper_info: dict, config: dict) -> Optional[Path]
#   - paper_info 包含 title, doi, 其他元数据
#   - config 包含 api keys, email, 输出目录等
#   - 成功返回 PDF 文件路径，失败返回 None

def try_download_from_arxiv(paper_info, config) -> Optional[Path]: ...
def try_download_from_semantic_scholar(paper_info, config) -> Optional[Path]: ...
def try_download_from_unpaywall(paper_info, config) -> Optional[Path]: ...
def try_download_from_crossref(paper_info, config) -> Optional[Path]: ...
def try_download_from_core(paper_info, config) -> Optional[Path]: ...
def try_download_from_pmc(paper_info, config) -> Optional[Path]: ...
def try_download_from_openalex(paper_info, config) -> Optional[Path]: ...
def try_download_from_chemrxiv(paper_info, config) -> Optional[Path]: ...
```

### 4.2 调度器

```python
FAST_TIER = [
    ("arXiv", try_download_from_arxiv),
    ("Semantic Scholar", try_download_from_semantic_scholar),
    ("Unpaywall", try_download_from_unpaywall),
    ("Crossref", try_download_from_crossref),
]

DEEP_TIER = [
    ("CORE", try_download_from_core),
    ("PMC", try_download_from_pmc),
    ("OpenAlex", try_download_from_openalex),
    ("chemRxiv", try_download_from_chemrxiv),
]

def download_paper(paper_info: dict, config: dict) -> DownloadResult:
    """尝试所有渠道下载论文，返回下载结果。"""
    
    for name, func in FAST_TIER:
        if not _channel_available(name, config):
            continue
        try:
            path = func(paper_info, config)
            if path and _validate_pdf(path):
                return DownloadResult(
                    status="downloaded",
                    source=name,
                    path=path
                )
        except Exception as e:
            logger.warning(f"[{name}] 下载失败: {e}")
    
    if config.get("fast_only"):
        return DownloadResult(status="manual_download_needed")
    
    for name, func in DEEP_TIER:
        if not _channel_available(name, config):
            continue
        try:
            path = func(paper_info, config)
            if path and _validate_pdf(path):
                return DownloadResult(
                    status="downloaded",
                    source=name,
                    path=path
                )
        except Exception as e:
            logger.warning(f"[{name}] 下载失败: {e}")
    
    return DownloadResult(status="manual_download_needed")
```

### 4.3 PDF 验证函数

```python
def _validate_pdf(path: Path) -> bool:
    """验证下载的文件确实是 PDF 而非 HTML 页面。"""
    try:
        with open(path, 'rb') as f:
            header = f.read(5)
        return header == b'%PDF-'
    except Exception:
        return False
```

### 4.4 渠道可用性检查

```python
def _channel_available(name: str, config: dict) -> bool:
    """检查某个渠道是否可用（是否有所需的配置）。"""
    if name == "Unpaywall" and not config.get("unpaywall_email"):
        return False
    if name == "CORE" and not config.get("core_api_key"):
        return False
    if name == "chemRxiv" and not config.get("enable_chemrxiv", True):
        return False
    # Crossref 和 OpenAlex 不需要强制配置
    return True
```

---

## 5. 下载日志增强

在现有日志格式基础上，新增 `source` 列，记录每篇论文实际从哪个渠道下载成功。

### download_log.csv 新增列

```
title, doi, status, source, timestamp
"Paper A", "10.1234/xxx", "downloaded", "Semantic Scholar", "2025-01-01 12:00:00"
"Paper B", "10.5678/yyy", "manual_download_needed", "", "2025-01-01 12:00:05"
"Paper C", "", "downloaded", "arXiv", "2025-01-01 12:00:10"
```

### download_summary.md 新增统计

在现有摘要末尾添加按渠道的下载统计：

```markdown
## 渠道命中统计
| 渠道 | 成功下载 |
|------|---------|
| arXiv | 3 |
| Semantic Scholar | 2 |
| Unpaywall | 1 |
| Crossref | 0 |
| CORE | 1 |
| PMC | 0 |
| OpenAlex | 1 |
| chemRxiv | 0 |
```

---

## 6. 错误处理要求

- 每个渠道函数必须用 try-except 包裹，**单个渠道失败不能中断整个流程**
- HTTP 请求统一设置 timeout=15s（快速层）/ 30s（深度层）
- 对 429（速率限制）做简单退避：sleep 2s 后重试一次，仍然失败则跳过
- 所有网络错误记入 debug 日志，不打印到用户终端
- 下载的文件必须通过 `_validate_pdf()` 验证，如果不是 PDF 则删除文件并视为失败

---

## 7. 测试检查清单

- [ ] 仅有标题、无 DOI 的论文：arXiv → Semantic Scholar（标题搜索）→ chemRxiv 路径正常
- [ ] 有 DOI 的论文：所有渠道按顺序尝试
- [ ] 不提供 `--unpaywall-email`：Unpaywall 被跳过，Crossref 和 OpenAlex 仍正常（只是不在 polite pool）
- [ ] 不提供 `--core-api-key`：CORE 被跳过
- [ ] `--fast-only` 模式：只跑快速层四个渠道
- [ ] 下载到 HTML 而非 PDF 的情况：被 `_validate_pdf` 拦截，标记为失败
- [ ] 429 速率限制：退避后重试一次
- [ ] 日志中 source 列正确记录命中渠道
- [ ] 已有 PDF（already_downloaded）不重复下载
- [ ] 所有渠道全部失败：标记 manual_download_needed，流程继续
