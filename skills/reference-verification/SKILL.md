---
name: reference-verification
description: 核对科研引文对应对象，提取原始引文、查询登记记录、比较字段并按需导出证据。保留中文原题名、条目与覆盖，不将技术状态等同文献真假。
license: MIT
metadata:
  version: "2.0.0"
---

# 文献证据核验

依据取得的资料确认被引用对象、对应关系与重要冲突；对可证明和未能证明的部分分别说明。结论的确定程度与当前证据相匹配，提取或检索的技术状态不自动等于文献真假。

根据当前缺口选择查询，不默认逐条穷举所有库。使用已安装的 `citrusscholar-support[refcheck]==2.0.0`，脚本输出机器 JSON，诊断写 stderr。网络必须显式授权；无授权仍可提取、比较、读取已存证据和导出。不要猜 DOI，不把候选记录直接说成已确认，不做论文论点支持性审查。

- `scripts/extract.py INPUT`：同一 inventory 的原始引文、位置和不确定字段。
- `scripts/verify_one.py --claims JSON --fixture PATH`：证据与差异；真实登记库调用需显式 `--allow-network`。
- `scripts/verify_batch.py --repository PATH --owner ID --session ID --inventory PATH --fixture PATH`：持久审计；后续使用原 audit-id 续跑。
- `scripts/render_report.py --repository PATH --owner ID --session ID --audit-id ID --format json|xlsx|ris|bibtex --output NEW_PATH`：只用已存证据。

读取 references/ 下相关方法。主 Agent 自主决定下一步，不受技能限制其他合法工具；专业步骤不新建另一个 Agent。
