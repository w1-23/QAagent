# 词法精度、坐标与覆盖

读取器生成 TableEnvelope v2：源 sha256、source_name、sheet、header_row、原始 row_numbers/column_numbers、values、parser_limits、precision_evidence、逐列 lexical_coverage 和解析 coverage。所有这些可以 JSON 往返；不以 DataFrame.attrs 的布尔值保证原始精度。

precision_evidence 是 `{row,column,raw,places,exp}` 的实际 token 列表。只接受原读取器保留、与值相符的数值字符串，指数与小数位均验证。CSV 中 `100.00` 保存为原 token。Excel 数值 100 即使 number_format 为 `0.00`，也不能认定其原始输入是 `100.00`。逐列 coverage 区分 preserved/partial/unavailable/not_applicable，并记录 token 数及原行。

行号和列号始终是原表坐标，不是删除空行后的编号。缺失行保持序列断点；原行不连续也打断窗口。表头选择错误时先修配置再运行，不迁移数据适配检测。

执行回执中未选、前提不足、无可用样本、解析截断、执行失败和实际完成必须区分。部分覆盖可以给观察，但不能声称完成整个文件。文件读入上限、单表行列和数值模式窗口限制以包返回为准。
