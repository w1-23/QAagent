# PaperConan 来源与借鉴说明

PaperConan 是独立开源项目：[zixixr/paperconan](https://github.com/zixixr/paperconan)，采用 MIT License。其公开数值审查规则为本项目设计部分确定性表格复核提示时的参考来源。

CitrusScholar 当前服务没有集成或调用 PaperConan 运行时：活动工具注册表不包含 PaperConan 工具，默认数据审查由本仓库的规则目录和 XLSX 标注器执行。服务发行包不需要 PaperConan 依赖；现行规则及其条件、样本要求和误报风险以 `data_audit_rules.py` 为准。

引用 PaperConan 的方法来源不表示本服务复现其全部扫描规则、覆盖能力或结论。审查命中仅用于人工复核，不是数据错误或不端认定。
