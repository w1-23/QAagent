# 语料注册与索引维护

运行期清单 `active_index.json` 指向一个版本化 `corpus_registry.json`。检索工具参数、Agent 提示中的语料类别、BM25/Dense 过滤和查询缓存代次都读取同一份清单。索引与分块按代次保存在 `DATA_DIR`；更新时先旁路构建、核验，再原子切换指针。

## 首次迁移

先备份数据目录，再用服务相同的 Python 环境执行全量重建。服务器应使用数据盘上的 `DATA_DIR`，不要把语料或权重放进源码目录。该迁移入口构建版本化 LanceDB；服务仍保留旧 Qdrant 只读兼容分支，但显式选择 Qdrant 时不会假装它兼容新代次。

```bash
python -m src.retrieval.corpus_admin reindex --all --cpu --activate
```

命令会检查每个分块的语料归属、稳定 ID、E5 `passage:` 向量、索引字段和行数。失败时活动索引不变；从已有版本化索引切换时，会把旧指针保留在 `active_index.previous.json`。首次从旧版 `data/lancedb` 迁移时，旧目录不会改写，但它的旧 schema 不能由新版检索器直接加载，因此回退需要停止服务、恢复迁移前源码备份，再启动旧版服务。重启后做代表性检索。

运行服务和索引维护需要与 LanceDB 版本配套的 Lance 格式绑定，以便按列扫描 ID 和语料映射而不读取整个向量列。当前服务器版本使用 `lancedb[pylance]==0.37.1`。

如果想先检查旁路代次，再独立切换：

```bash
python -m src.retrieval.corpus_admin reindex --all --cpu
python -m src.retrieval.corpus_admin activate --generation gYYYYMMDDTHHMMSSZ_ab12cd34
```

把第二条命令中的代次替换成重建输出的真实 ID。`activate` 会重新对账全部分块和向量 ID、家族、表集合及计数；它只在复核通过后更新活动指针。

## 登记新的语料族

```bash
python -m src.retrieval.corpus_admin registry register-family \
  --family-id pest_disease --display-name "柑橘病虫害资料" \
  --description "病虫害识别、防治与监测资料" \
  --routing-term "病虫害" --routing-term "citrus disease" \
  --topic "病理与抗性"
```

若要增加该族可用于筛选的主题：

```bash
python -m src.retrieval.corpus_admin registry register-topic \
  --family-id pest_disease --topic "病理与抗性"
```

登记会生成新清单版本，不重建或修改向量表。它本身不使该语料族可检索；只有导入至少一个语料后才会进入可用检索范围。

## 导入或更新一个语料

语料族必须已登记。每次导入都会按 corpus ID 替换该语料的完整分块和向量表；相同目录内容重复导入会产生相同 document/chunk ID，不会向原表追加重复记录。

```bash
python -m src.retrieval.corpus_admin ingest \
  --dir /data/import/citrus_disease \
  --corpus-id citrus_disease_2026 \
  --family-id pest_disease \
  --display-name "柑橘病虫害资料 2026" \
  --topic "病理与抗性" --cpu --activate
```

支持 PDF、TXT、Markdown。PDF 仅导入可提取的文字。命令复制当前活动 LanceDB 到新代次，替换目标 corpus 表，校验整库表集合/计数/ID/家族元数据后切换。输入解析失败或校验失败不会替换活动指针。

## 检查与回滚

```bash
python -m src.retrieval.corpus_inventory --data-dir "$DATA_DIR"
python -m src.retrieval.corpus_admin rollback
```

清单变更、导入和重建由数据目录锁串行执行。在版本化索引间回滚会先核对上一版清单、分块与向量表一对一，再切换指针；验证失败时保持当前版本。首次迁移前的旧版索引属于旧 schema，不能由该命令直接激活为版本化代次。首次迁移完成并确认新索引稳定后，可将旧版索引和失败构建代次移至归档区保留；不要直接删除唯一回滚副本。

首次迁移后，可把旧版 `data/lancedb` 和没有 `index_manifest.json`、且未被活动指针引用的失败构建代次移到 `DATA_DIR` 之外的归档目录；不要删除它们。当前活动代次及其 `corpus_generations/<generation>` 分块文件必须保留。若要恢复旧版服务，需要同时恢复匹配的旧源码与旧 LanceDB；新版服务不能直接读取旧 schema。

## 检索范围参数

`search_bm25_filtered()` 与 `search_dense_filtered()` 使用相同语义：`corpus_types=None` 表示当前已加载的全部语料族；显式传入空列表 `[]` 表示不检索任何语料；传入未知语料族会报错。调用方若要限定范围，应显式传入语料族，不能依赖空列表代表“全部”。

## 索引契约

- 分块 `schema_version=2`，必需稳定 `corpus_id`、`corpus_family_id`、`document_id`、`chunk_id`、来源和正文。
- `chunk_id = SHA-256(corpus_id, document_id, chunk_index)` 的稳定截断值；BM25、Dense、RRF 与重排按该 chunk 映射。
- 向量编码器固定为配置中的 E5 模型；文档用 `passage:`，查询用 `query:`，前缀重复时规范化为单个。
- 每张向量表保存 `chunk_id`、`corpus_id`、`corpus_family_id` 与主题分类；Dense 在数据库候选查询阶段过滤，而非先取全库 Top-K 再筛。
- BGE 重排模型独立于 E5 编码模型。模型、维度、索引 schema 或前缀契约不匹配时拒绝加载，不静默混用。
- 原始语料字段仅在旧数据迁移边界映射一次。未知家族、未知主题、缺字段或冲突 ID 会被拒绝，不自动归入文献类。
