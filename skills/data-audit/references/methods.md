# 七组方法与选择前提

完整可机读目录由 `python scripts/catalog.py` 返回，共 38 项，包含覆盖诊断。每条的 requested/tested/skipped/reasons 和输入范围是事实依据；目录 available 不代表运行。

1. **结构、缺失与类型**：empty_table、empty_column、missing_values、missing_cell、constant_field、mixed_numeric_text、non_numeric_cell、multi_header_suspected、optional_column、required_field_absent。核对 header_row、必填/可选字段；备注列不自动视为测量列。缺失比例和常量可能符合设计。
2. **记录与键重复**：duplicate_row、duplicate_key、duplicate_composite_key。主键/复合键必须来自用户或数据字典；长表、技术重复和共享对照可能合法。不把相同记录直接判为错误。
3. **范围、关系与探索性离群**：out_of_range、logic_conflict、iqr_outlier。范围和逻辑使用显式配置，不编造物种/时期合理界限。IQR 至少 8 个有效值/组；它是探索性观察，偏态、生物差异、单位变化需复核。iqr_insufficient 单列覆盖，不标成异常。
4. **同行列关系与数值模式**：identical_columns、constant_offset_columns、constant_ratio_columns、exact_linear_columns、adjacent_equal_differences、sliding_equal_differences、repeated_sequences、repeated_blocks。列关系只比较共同有效原行；有序模式需要 order_confirmed、columns 及窗口/容差配置。缺行/缺值断窗，不各自去空后 zip。网格、派生公式和周期信号可合法触发。
5. **报告精度与尾数**：raw_numeric_tail 使用实际 token 的小数位/指数分层，每层默认至少 12 个 token、8 个不同值，描述频率不冒充 p 值；100 的末位不能通过 rstrip('0') 变成 1。decimal_ending_concentration 只审格式化值，明确格式化六位的限制，显式选择才执行；不能替代 raw_numeric_tail。
6. **实验设计与重复结构**：unequal_group_size、unequal_replicates、expected_experiment_structure。明确因子、实验单位、重复、排除规则和预期组合。不假定平衡设计；缺映射时 design_checks_skipped/invalid_group_mapping/invalid_value_mapping 是覆盖说明。
7. **整数汇总必要条件**：integer_mean_inconsistent、integer_summary_inconsistent。先确认观测为整数、n 定义、报告精度、样本或总体 SD。使用精确有理数与舍入区间；必要条件通过不证明原始样本可实现或数据真实。integer_summary_not_checked/grim_sd_skipped 表示输入或口径不足。

findings_truncated 表示条目上限导致输出不完整，不能解释成未列区域无问题。跨文件数值复用与推断统计由包的显式 API 按需提供；本技能 CLI 不自动扫描目录或补造实验单位。
