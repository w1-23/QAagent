# 本轮源码工程改造交付包（2026-11-12）

范围：通用 Word 导出与交付工程能力的源码改造 + 隔离工程测试。**未运行**三个真实科研案例，**未重跑**原 FW20/40，**未提交/推送**，**未重启服务**。

## 文件

| 文件 | 内容 |
| --- | --- |
| `P0_source_audit.md` | 基线事实、架构定位、根因与逐项修复、测试证据、未覆盖边界 |
| `P0_manifest.csv` | 变更文件的路径、新增/修改、字节数与 SHA-256 |
| `WORD_TOOL_API.md` | `word_document` / `export_table` 接口、中断恢复、兼容与边界 |
| `source_changes.patch` | `git diff --binary` 全量补丁（新增文件已 `git add -N -f` 登记，未 commit） |
| `run_checks.py` | 离线回归入口，三组各自独立进程 |
| `make_manifest.py` | 重新生成 `P0_manifest.csv` |
| `main.log` / `main.xml` | 主回归组日志与 JUnit |
| `word_download.log` / `.xml` | 真实账号下载接口验证 |
| `attachment_boundaries.log` / `.xml` | 既有附件边界验证 |

## 复跑

```bash
python engineering/word_delivery_20261112/run_checks.py
```

需要 `PYTHONPATH` 指向 `01_src`（脚本已自行设置）。全部使用临时目录与合成文本，无模型请求、无网络、无真实科研数据。

## 关键交付位置

- 受控 Word 核心：`01_src/src/core/word_documents.py`
- Word 工具适配：`01_src/src/research/word_tools.py`
- 已安装 Skill：`01_src/src/prompts/skills/word-export/SKILL.md`（与用户提供的 `01_内置_WORD_SKILL.md` 逐字节一致，SHA-256 `5e987ae7c38647918cb18e7e0013904d1aaaef4fcccccb367eb4c8130c8ccfab`）
- 通用表格导出：`01_src/src/research/tools.py::export_table`
- 交付契约与匹配：`01_src/src/graph/export_contract.py`
- 成功后简短气泡：`01_src/src/graph/document_delivery.py`

## 生效方式

源码改动需要受控重启服务后才会在运行实例中生效。本轮未执行重启，也未确认当前进程的内存版本；磁盘上的 `DEPLOYED_RECORD.json` 仍指向 `8a40d77`。
