# CitrusQA 本地端到端测试结果

- 测试时间：2026-10-25T22:03:16+08:00 至 2026-10-25T22:07:37+08:00
- 服务：`http://127.0.0.1:8011`（账号、会话和导出目录均为隔离测试数据）
- 模型：`aliyun_bailian` / `deepseek-v4.1-flash-1017`；图片输入声明：`True`
- 新会话：`b45fdd34-de67-4b6f-8352-79153ba57e52`；新注册账号角色：`user`
- 密码、API Key 未写入结果文件。

## 主要验收

| 检查项 | 结果 |
|---|---|
| registration_regular_user | 通过 |
| session_created | 通过 |
| all_completed_turns_restore_timer | 通过 |
| history_saved_before_done | 通过 |
| image_chat_completed | 通过 |
| ordinary_upload_did_not_audit | 通过 |
| data_audit_tool_called | 通过 |
| data_audit_parameter_errors_absent | 通过 |
| data_audit_completed | 通过 |
| local_retrieval_attempted | 通过 |
| local_only_request_did_not_call_web | 通过 |
| reference_verifier_called | 通过 |
| web_search_called | 通过 |
| web_fetch_called | 通过 |
| web_evidence_ledger_saved | 通过 |
| svg_tool_called | 通过 |
| svg_validation_succeeded | 通过 |
| paper_export_succeeded | 通过 |
| svg_embedded_in_downloaded_html | 通过 |
| standalone_svg_downloaded | 通过 |
| corrupt_upload_rejected | 通过 |
| artifact_downloads_succeeded | 通过 |

## 请求逐项结果

| 项目 | 完成 | 秒 | 工具 | 刷新后耗时 |
|---|---:|---:|---|---:|
| 01 普通问答与刷新计时 | 是 | 2.79 | 无 | 恢复 |
| 02 实际图片输入 | 是 | 8.07 | 无 | 恢复 |
| 03 文件上传不自动审查 | 是 | 7.85 | deepseek_search | 恢复 |
| 04 本地英文混合检索 | 是 | 8.0 | local_hybrid_search | 恢复 |
| 05 PaperConan 全集数值审查与导出 | 是 | 160.12 | inspect_uploaded_table, audit_uploaded_data, deepseek_fetch | 恢复 |
| 06 PDF 引用 DOI 核验 | 是 | 31.36 | deepseek_search, verify_uploaded_references, deepseek_fetch | 恢复 |
| 07 Web Search 与 Web Fetch | 是 | 11.52 | deepseek_search, deepseek_fetch | 恢复 |
| 08 安全 SVG 绘图与文件导出 | 是 | 31.34 | draw_svg, export_paper_analysis | 恢复 |
| 损坏 XLSX 上传边界 | 是 |  | 无 | 不适用 |

## 下载产物

- `8c0ba38b60c34faea1bfd904a487e74d_合成_行向与局部复用_科研数据审查.xlsx`：HTTP 200，17372 字节，SHA-256 `d6c1c30d9bdb0bc64d369d8c6834894636dbbfbb0fd711796916affcaa8d892f`
- `2334f331b9d84894a34935d55308164d_合成_行向与局部复用_科研数据审查_详细视图.html`：HTTP 200，123456 字节，SHA-256 `9841fdbcb578553aee387a7953d9730f07a06864209ecc8255d79d502f31b7a8`
- `9f9270ec3b6e4ea1995aed56b4e2c996_合成_行向与局部复用_科研数据审查_机器可读结果.json`：HTTP 200，78136 字节，SHA-256 `a0c2853c656932f6df82d8492c6aa84fdf5e6de053d85c3ff8adfba5e74ae0ab`
- `44e65e8da3aa46d0b901a73545663096_合成_行向与局部复用_科研数据审查.md`：HTTP 200，29462 字节，SHA-256 `e080c07c0e2104e715b68e810e7ef702c3deff4c1024e41e909c1442551ffba1`
- `22edc3d41e8f4383913df7387e4617a2_合成_复杂数值模式_科研数据审查.xlsx`：HTTP 200，25134 字节，SHA-256 `0ebdf1244160ca38e49c648182ca2d927f4360cf3b49396cb6c1cd77a79a3763`
- `1c5e159d3adf4cfa94e24454d5e73c43_合成_复杂数值模式_科研数据审查_详细视图.html`：HTTP 200，708018 字节，SHA-256 `ffe479e5ce4b31b6b60fc33fc53fc111a558bc2b37e9fea21f554e2b9e50a442`
- `65ba44792f164480be6ada58523fba85_合成_复杂数值模式_科研数据审查_机器可读结果.json`：HTTP 200，272716 字节，SHA-256 `f17b8bc1a3d3efb94c758a034ec02098f47db18e602ab475748df5dd878af010`
- `d7dc46f71bca45cd86c32b20262432fb_合成_复杂数值模式_科研数据审查.md`：HTTP 200，24202 字节，SHA-256 `95f8b7b52e0c880cc9d97b794f88797fcc9fe5a16f3d5d2f064cf69aec5a15d8`
- `c5fc4a577cf74e37bc65b920604f5130_合成_跨文件错位关系_科研数据审查.xlsx`：HTTP 200，11755 字节，SHA-256 `4e8de1052308c6b67497faaa57e9d95f62d149fda940c120ba8d25850b6765b7`
- `adcd50d953a34edebdc051ee4ae8c74b_合成_正常对照与单位换算_科研数据审查.xlsx`：HTTP 200，7932 字节，SHA-256 `5d02871cd948643aa5b040bda27457bb055a140f16d155793dc66399f861c59e`
- `a79c798d54fb4441b2e984f6f1ca5f7f_合成_行向与局部复用_科研数据审查.xlsx`：HTTP 200，10790 字节，SHA-256 `27c1a12f1cabc0f24306bb83e5826370b371e614d298a5972f1cf424141821f4`
- `03b3f52155d7442c95ba66ad4494f400_合成_正常对照与单位换算_科研数据审查_详细视图.html`：HTTP 200，23270 字节，SHA-256 `02aa642e091c864650e396a9260857847db503ebfcd584bb7d02f8cb839a7196`
- `9040e6bf5dd244689e0501c052138f49_合成_行向与局部复用_科研数据审查_详细视图.html`：HTTP 200，63688 字节，SHA-256 `22a965b653b4338a40f172a380b17450f740db0faace11fe530fe4c3590b39bc`
- `fc45bb43beff48a88dfe5143b8038816_合成_跨文件错位关系_科研数据审查_详细视图.html`：HTTP 200，64149 字节，SHA-256 `8a2e8598a96966643e6c244747e2b685ed12df3e704fa756c6784159b43c67db`
- `86a09d708c6b41aa8e53971d3995c25d_合成_正常对照与单位换算_科研数据审查_机器可读结果.json`：HTTP 200，6666 字节，SHA-256 `fd53c3d0ff20e436ad60c2ff373ab204aac077bc223c95c98a1d52d833dde420`
- `063835bf30ad484393f255f1b610bdb7_合成_跨文件错位关系_科研数据审查_机器可读结果.json`：HTTP 200，45199 字节，SHA-256 `47068f236ccec4c2679e28abc5cab2d57480f88a1f3db71df69144fc61397820`
- `e4956b92a43a49c595663140489685da_合成_行向与局部复用_科研数据审查_机器可读结果.json`：HTTP 200，27733 字节，SHA-256 `12d0e3280379187e581b39acc4f09e02c841724ded430bc802f84068cf508c63`
- `45aadc1b2f5a42778280566124a778c9_合成_正常对照与单位换算_科研数据审查.md`：HTTP 200，1959 字节，SHA-256 `d2af5b5d8f0972a3699236701a3242180eb2934e30a00d5e31025e7939b0b12e`
- `b7b5fd40d1b94e069a5877ec91dff96f_合成_跨文件错位关系_科研数据审查.md`：HTTP 200，19697 字节，SHA-256 `120fbab2dac483dfdacb9b7d37147c78b8096b0f1b22ca87ecefa6074779fa54`
- `c7011bf2ebfa41ef9581e35e8542ce76_合成_行向与局部复用_科研数据审查.md`：HTTP 200，8414 字节，SHA-256 `4f16bb326da86e2ccc04cf5e37940f043a35b3f7490212734c7795ac34637c61`
- `48fb2e24eb3f4b2fa2a13216d7acd74a_合成_复杂数值模式_科研数据审查.xlsx`：HTTP 200，25134 字节，SHA-256 `03363d6b292defe1f242670bb24c31ba8207ef0368b01b7ed099198d1c28f072`
- `e534f90e128f47a78217d91f2cabf1b4_合成_复杂数值模式_科研数据审查_详细视图.html`：HTTP 200，708018 字节，SHA-256 `ffe479e5ce4b31b6b60fc33fc53fc111a558bc2b37e9fea21f554e2b9e50a442`
- `60c53243123b47439c926809afae8c95_合成_复杂数值模式_科研数据审查_机器可读结果.json`：HTTP 200，272716 字节，SHA-256 `866e98c562d70fcc369a400958a6a87b2ee5d8c364a8c2e291ec1b0b4b23983f`
- `72263bc0ffb34ed78d01b68c8aa1d53c_合成_复杂数值模式_科研数据审查.xlsx`：HTTP 200，25950 字节，SHA-256 `3f819f5aefc0e41720fbdf3743ab01ae27f52d260d9f9bdbe33716563b926215`
- `4f030cbb99db4dc4bf648ec868a8639f_合成_复杂数值模式_科研数据审查.md`：HTTP 200，24202 字节，SHA-256 `95f8b7b52e0c880cc9d97b794f88797fcc9fe5a16f3d5d2f064cf69aec5a15d8`
- `3b46e670b789405a80f037c5bf6cfd50_合成_复杂数值模式_科研数据审查_详细视图.html`：HTTP 200，708018 字节，SHA-256 `ffe479e5ce4b31b6b60fc33fc53fc111a558bc2b37e9fea21f554e2b9e50a442`
- `aa54f41e65344bbfa135db9bfce14e09_合成_复杂数值模式_科研数据审查.xlsx`：HTTP 200，25950 字节，SHA-256 `3f819f5aefc0e41720fbdf3743ab01ae27f52d260d9f9bdbe33716563b926215`
- `907fe64d80684815aa374f7610b0e189_合成_复杂数值模式_科研数据审查_机器可读结果.json`：HTTP 200，272716 字节，SHA-256 `397cb633f00bf494b5d07ffec78ff5f6d07e36a0fdaa7a2aa3d20d1fcdb7d993`
- `be3a5dfa5bc541648956a42bd891467c_合成_复杂数值模式_科研数据审查.md`：HTTP 200，26411 字节，SHA-256 `a13baf34baa30aa4d280720899b8cc335cd17986f6fc815c3597802df84106a2`
- `de8ad60c5bac4cf3a47ed695812c2d93_合成_复杂数值模式_科研数据审查_详细视图.html`：HTTP 200，708018 字节，SHA-256 `ffe479e5ce4b31b6b60fc33fc53fc111a558bc2b37e9fea21f554e2b9e50a442`
- `a4d6c1ada7e449cbb2b4cfc95b6d64fb_合成_复杂数值模式_科研数据审查_机器可读结果.json`：HTTP 200，272716 字节，SHA-256 `cd5dfacf73e639d9bef467d33dea747b861c0eee4a5e0f9ae18d3b34e9dddfef`
- `a665fc7f367a4467b54aea2c36369227_合成_复杂数值模式_科研数据审查.md`：HTTP 200，26411 字节，SHA-256 `a13baf34baa30aa4d280720899b8cc335cd17986f6fc815c3597802df84106a2`
- `ffd3952590ff43ab9a66fdf41ac5eab0_合成_复杂数值模式_科研数据审查.xlsx`：HTTP 200，36004 字节，SHA-256 `cc68fdb204863be9de4b68e45d1472ef426d6700df044bde767698abf7f82789`
- `eef2d650980948c4aa15070197a335f6_合成_复杂数值模式_科研数据审查_详细视图.html`：HTTP 200，818894 字节，SHA-256 `1ea77e1b594c76ec447753070a976bcc108cb799d472e5a38f4658e00dcdf755`
- `b40880c46ddf4b098e847dfa0ab4057c_合成_复杂数值模式_科研数据审查_机器可读结果.json`：HTTP 200，350976 字节，SHA-256 `b430d3d858f38bb20b3372ca23f3e240fc7e220115371634e7df65b69ad8b8c3`
- `944b2460bca24e1e8463a4844644fe3f_合成_复杂数值模式_科研数据审查.md`：HTTP 200，54119 字节，SHA-256 `e4f21eab1a53801d2434ba1b3478fc52b9b683c4ab1ccc422070dfb328b4b9d7`
- `ac335ddfe5214a8fb0e483e28757de26_合成论文及引用_DOI核验.xlsx`：HTTP 200，5523 字节，SHA-256 `ca5e86b8141c16cf45a8ca00a5479f23206f12777a0a94981f92941e786f7f1f`
- `f76de1fe958a43f38e2c3923908cba04_科研数据审查流程图.html`：HTTP 200，2472 字节，SHA-256 `774d475acf383a05dcf789c58efe0d50ed9bcb7ad8b1854f40d0c34507a4778d`
- `176dd649904b4c65bfa33daa4e0a8646_科研数据审查流程图.svg`：HTTP 200，1337 字节，SHA-256 `65caae39341671816f3a28eef8af3e894e1371532ad9866b168323f3d6dc8c1a`

## 验证方式与限制

每个完整回答完成后，测试脚本立即请求会话消息接口，模拟页面刷新；对照实时 `done.gen_time_ms`、恢复消息中的 `gen_time_ms` 和最后一条回答内容。
损坏 XLSX 上传通过 API 直接验证边界。其余路由由真实配置的主模型自主选择，因此工具未被调用会作为未触发记录，不伪报通过。
测试文件均为合成数据；报告中的模式只代表筛查线索。联网状态以本次工具回执为准。
本地检索诊断：query_language=english，translation_used=False，BM25=48，Dense=48，RRF=86，selected=8，reranker_fallback=False，耗时=2729.9 ms。
Web Fetch 被网络目标校验拦截的调用轮数：1；这表示抓取通道受阻，不表示目标页面不存在。
本地检索使用明确英文文献请求单独验证；Dense/LanceDB 或重排器不可用时记录实际降级情况，并核对‘仅本地’请求没有调用 Web 工具。

## RAG 恢复专项验证

- 在实际服务 Python `E:\anaconda\python.exe` 中安装并验证 `lancedb==0.37.1`。
- LanceDB 可列出并打开全部 **11 张表**，合计 **187,441 行**；混合检索启动时 `failed_batches={}`。
- 使用英文查询 `Citrus Huanglongbing detection symptoms and management`：BM25 命中 8 条、Dense 命中 8 条、RRF 合并 86 个候选；完整混合检索得到重排结果，`reranker_fallback=False`。
- E5 首次冷启动向量计算约 8.0 秒；紧接着的混合检索约 8.9 秒，含完整服务初始化约 27.7 秒。E2E 实际请求中模型已预热，混合检索约 2.7 秒。
- 本地使用 E5 `intfloat/multilingual-e5-large` 编码查询，使用 BGE `BAAI/bge-reranker-v2-m3` 独立重排；这两种模型用途不同，不应要求模型名相同。缓存固定在 `agent/.hf_cache/`，请求会话目录不会覆盖缓存目录。
- 本轮没有重建或写入向量表；E5 入库向量缺少 `passage:` 前缀仍列为后续索引迁移评估，不在本轮修改。

## 需要留意

- 合成数据完整审查与导出用时约 160 秒，虽然在本轮请求时限内完成，已接近当前 180 秒总时限；复杂多文件审查后续可优化工具路由与报告生成耗时。
- Web Search 成功运行；Web Fetch 对本次官方 Crossref 页面返回目标地址安全校验失败。回答和轨迹保留了“未取得页面”的状态，没有将抓取失败表述为没有相关结果。

完整回答和历史：`results.json`、`session_messages.json`；安全执行轨迹：`safe_sse_log.jsonl`；下载校验：`downloads_index.json`。
