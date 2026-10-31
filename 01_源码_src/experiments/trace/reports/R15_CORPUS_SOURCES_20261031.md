# R15 本地原文溯源与元数据回填 · 交付记录

日期：2026-10-31
基线：`54a0160`（本轮验收后的 HEAD 见 §6）
环境：本机 `8010`（Windows，`E:\codex_WORKSPACES\Citrus_QA_Agent`）；staging `8011`（Linux，`/home/WZQ/staging_citrus`）
生产 `8000`：**本轮未部署、未 push**

---

## 1. 需求

1. 回答里出现的本地文献标识应可点击，打开**本地原文**（不是联网链接）。
2. 本地/联网来源**不必**按 `[n]` 编号体系嵌入正文；只要能跳网页、能开本地原文即可。
3. 打开本地原文时**高亮**命中片段。
4. 「映射做对，少兜底」：把入库语料的题名/年份/期刊映射补齐，减少「未具题名文献」。
5. 资料库支持多列；Loading 文字整句浮动（观感项）。

## 2. 关键事实（先查证再动手）

| 事实 | 证据 |
|---|---|
| 服务器与本地**都不存在文献 Markdown 原文** | `agent/data/cqa_literature/` 只有 `chunks.jsonl`（105,997 行）+ `metadata.json`；`find -name '*.md' -path '*literature*'` = 0 |
| 「原文」只能按分块还原 | 每篇按 `chunk_index` 升序、`section_name` 分节拼回，如实标注「按分块顺序还原全文」 |
| 题名确实是在切块时丢的 | 无题名文献的 `chunk_index=0` 行直接从 Abstract 正文开始，`section_name='Abstract'`，`title` 填的是内部键 |
| 回答里的橙色标识来自 Markdown 行内代码 | `app.css:387` 对 `.content code` 上色；前端原本没有识别 `paper_*` 的专门路径 |
| 写入侧清洗让新回答不再带编号 | `src/session/manager.py` 写入时 `scrub_internal_keys`；读侧此前也清洗 → 历史里没有可点的键 |

## 3. 实现

### 3.1 元数据回填

- `src/core/corpus_source.py`：sidecar 读取（`metadata_index.json`，mtime+size 缓存，异常退化为 `{}`），`title_label/year/journal/doi` 优先取 sidecar；外部反查来源（`crossref_doi`/`europepmc`/`crossref`）时**年份与 DOI 以 sidecar 为准**，避免语料行里的错年份覆盖。
- `experiments/corpus_meta/build_metadata_index.py`：流式只读 `chunk_index=0` 行；题名优先级 = 非章节词的 `section_name` → Markdown 首行/标题 → 非内部的 `title`；`parse_key_meta` 从 `paper_id` 尾部解年份与期刊；重建时**保留**外部反查题名。
- `experiments/corpus_meta/recover_titles.py`：对仍无题名的文献走 Europe PMC `ABSTRACT:(w1 AND w2 …)`（8 个罕见长词）→ 8-gram 重叠校验 → Crossref 兜底；响应缓存可离线重跑。
- 结果（服务器实测，约 20 分钟）：

| 指标 | 回填前 | 回填后 |
|---|---|---|
| 有题名文档 | 3,773 / 4,910（76.8%） | **4,733 / 4,910（96.4%）** |
| literature 有题名 | 2,034 / 3,171 | 2,994 / 3,171 |
| sidecar 体积 | 1.88 MiB | 2.34 MiB |
| 仍无题名 | 1,137 | **177**（多为非 PMC 收录期刊/中文刊，继续诚实兜底） |

### 3.2 本地原文打开 + 命中高亮

- 新增 `GET /api/v2/corpus/source?record=<key>`（`src/core/corpus_source.py` + `src/api/main.py`）：登录校验、键格式校验、按语料顺序查找、方法库需管理员、404 语义明确；返回 `sections[]` 与还原后的 `markdown`，不返回任何绝对路径。
- 新增 `web/assets/corpus_reader.js` / `corpus_reader.css`：抽屉式阅读器、渲染视图/原始视图、DOI 跳转、复制、Esc/遮罩关闭；`openCorpusSource(key, {focus})` 时把命中片段（来源条目的 220 字摘要）高亮（`markFocus` + `.cr-hit` + 「已定位到命中片段」）。
- 内联标识可点击：`web/assets/app.js` 的 `renderMarkdownSafe` 末尾挂 `linkifyCorpusKeys`。

修掉的三个真缺陷：

1. **键被截断**（旧正则 `(?:_[A-Za-z0-9]+){2,}` 吞掉尾部 `_`）→ 换严格形状 `paper_[0-9a-f]{8}_\d{6}_[A-Z]_\d{4}_[A-Za-z0-9_]{2,}`，用 sidecar 的 4,910 个键验证 **paper_ 键 0 个不匹配**。
2. **嵌套锚点**（行内代码分支先生成 `<a>`，文本分支又包一层）→ 占位符 `\u0000CR<n>\u0000` 分离两段处理，实测 `nestedAnchors=0`。
3. **误判**（`paper_not_a_key` 被当文献键）→ `_corpusKeyOnly` 要求整段匹配才算。

### 3.3 按轮次回查来源（不依赖模型输出）

- 新增 `GET /api/v2/session/{sid}/turn-sources?request_id=`（`src/core/turn_sources.py` + `src/core/tool_results.py::fetch_by_request`）：从 `tool_results` 的**原始工具输出**解析本地键与联网 URL，本地优先、配额 8 + 6、上限 12。
- 前端在气泡下方渲染「本轮本地来源 / 本轮网络来源」胶囊：本地点击开原文并按 snippet 高亮，联网 `target=_blank`。

### 3.4 展示路径与模型上下文分离（关键取舍）

| 通道 | 内容 | 目的 |
|---|---|---|
| `/messages`（`for_display=True`） | **原样保留** `paper_*` 键 | 前端渲染成可点击橙色标识 |
| `context-detail` / Agent 上下文（默认） | `scrub_internal_keys` 清洗 | 编号不进入模型提示词 |

契约测试：`src/api/test_display_scrub_split.py`（6 例）。

### 3.5 观感项

- 资料库多列：`session_files.js` 的 `LIB_COL_TIERS=[[980,4],[700,3],[440,2],[0,1]]`，实测面板内容宽 315/515/715/995px → 1/2/3/4 列。
- Loading 文字浮动：`loading-sprout.css` 整句 `translateY` 周期约 2.5s（实测 `0 → -1.32 → -2.49 → 0`），`reduce-motion` 下静止。

## 4. 验收（真实浏览器 CDP + 真实语料）

| 项 | 数据 |
|---|---|
| 内联标识（历史渲染） | 2 个锚点、键完整（含尾部 `_`）、`nestedAnchors=0` |
| 点击打开 | `Nature Genetics · 2017 年 · 24 段 · 30,810 字`，正文 31,278 字，标题为真实题名 |
| 颜色/交互 | `rgb(165, 86, 15)`、虚线下划线、`cursor:pointer` |
| 三个真实键 | Nature Genetics 2017 / BMC Plant Biolog 2018（DOI 10.1186/s12870-018-1369-3）/ DNA research 2021（DOI 10.1093/dnares/dsab014） |
| 无效键 | 「本地原文不可用」，无路径泄漏 |
| 来源胶囊 | 12 条 = 本地 8 + 网络 4，标签无裸键、无序号 |
| 测试 | `pytest src` → **681 passed** |

## 5. 决策记录（2026-10-31）

**新回答不保留裸标识，改用气泡下方「本轮本地来源」胶囊；历史回答保留标识并可点击。**

理由：新回答正文里出现 `paper_45bbcac3_000004_B_2017_Nature_Genetics_` 这类长串可读性差（此前也收到过同样反馈）；
而来源胶囊显示的是真实题名与期刊年份，信息量更高。历史回答保留原键，是为了让已存在的讨论仍能一键回到原文。
两条路径都指向同一个阅读器（`window.openCorpusSource`）。

- 写入侧：`src/session/manager.py` 清洗 ai 正文（新回答无裸键）。
- 读侧：`/messages` 用 `for_display=True` 保留历史原键；模型上下文仍清洗。
- 新回答的来源入口：`GET /api/v2/session/{sid}/turn-sources?request_id=` → 本地/网络两组胶囊。

## 6. 部署与线上复核（2026-10-31 21:08–21:20）

| 项 | 结果 |
|---|---|
| 备份 | `/home/WZQ/CitrusQA/backups/pre_r15_20261031_210743`（32 MB，含 `src/`、`web/`、旧 sidecar） |
| 上传包 | `_incoming/prod_r15.tar.gz`（15 文件 / 3.12 MB，全部 LF 归一） |
| 启动 | pid `258083`，命令与既有部署一致（`--proxy-headers --forwarded-allow-ips=127.0.0.1 --workers 1`） |
| 冒烟 | `/health`=200、`/`=200、`app.js?v=30` / `corpus_reader.js?v=3` / `session_files.js?v=5`、三个新接口未登录均 401、启动日志 ERROR/Traceback 0 |
| 线上元数据 | `meta_stats = {documents: 4910, titled: 4733, untitled: 177}`；`paper_3fff7da9…` → 2015 + DOI `10.1093/molbev/msv082` |
| 读侧双通道 | 线上 4 个含键会话：`model_hits=0`（全部清洗）；`b0c14c1e` `display_hits=4`（完整键保留、可点） |
| `DEPLOYED_COMMIT` | `9270fe1`（并删除陈旧的 `01_源码_src/DEPLOYED_COMMIT`） |

### 7.1 部署中发现的第三个缺陷：被截断的键残片

`paper_3b4f811a_000562` 这类残片（旧前端正则吃掉尾部 `_` 的产物）既点不开，也没被清洗——历史里有 9 条 ai 正文受影响，
其中 2 个会话会把 `paper_` 残片带进模型上下文。修法：

- `record_label.py` 新增 `_PARTIAL_KEY_RE` / `_fragment_phrase` / `scrub_partial_keys`；`scrub_internal_keys` 末尾追加残片兜底。
- `manager.py` 展示态改用 `scrub_partial_keys`：**完整键保留（可点）**，残片换成「一篇本地文献记录（第 562 篇）」。
- 复测：model_hits 4 → 0；展示态残片消失、完整键仍在。

## 7. 未做 / 边界

- 生产 `8000` 未部署、未 push（等确认）。
- 177 篇仍无题名：继续显示「未具题名文献（期刊 · 年份 · 第 N 篇）」，**不伪造题名**。
- 原文为分块还原，非原始排版；公式/图表/脚注可能丢失。
- 普通用户访问方法库返回 403 由单测覆盖，未在浏览器做双账号实测。

## 8. 提交

```
7c72fd1 fix(session): 展示路径保留内部键以便点击，模型上下文仍清洗
7a5651b fix(corpus): 内联标识严格形状 + 取消嵌套锚点；三处真机缺陷
5b8afe6 feat(corpus): 题名反查落地（Europe PMC/Crossref）+ 年份以 sidecar 为准
4ea6911 feat(corpus): 元数据索引回填
c4d3d0d feat(corpus): 本轮来源可点开（turn-sources → 阅读器）
7bd7177 feat(ui): 资料库多列 + loading 文字浮动
```

staging `8011` 已同步到 `7c72fd1` 对应字节，`/health`=200、未登录接口 401。
