"""Rule catalogue and finding metadata for deterministic table review.

Signals are review leads, not findings of misconduct. Each rule records its
applicability, trigger, minimum sample where relevant, location method, and
known false-positive risks so both the service and generated workbook can
describe what was actually checked.
"""
from __future__ import annotations

from typing import Any, Iterable


_COMMON = "CSV/TSV/XLS/XLSX 工作表的已解析表格"

RULES: dict[str, dict[str, str]] = {
    "empty_table": {"applies_to": _COMMON, "trigger": "所选工作表没有数据行", "sample": "至少 1 个数据行", "location": "工作表级", "risk": "可能是表头行选择错误或有意留空"},
    "empty_column": {"applies_to": _COMMON, "trigger": "整列均为空", "sample": "整列扫描", "location": "列名及工作表", "risk": "常见于公式列、预留列或导出格式"},
    "missing_values": {"applies_to": _COMMON, "trigger": "非空/空值比例达到审查提示阈值", "sample": "整列扫描", "location": "列名", "risk": "缺失可能由实验设计或字段不适用造成"},
    "missing_cell": {"applies_to": _COMMON, "trigger": "非可选字段的单元格为空", "sample": "逐行", "location": "工作表、行、列对应单元格", "risk": "该字段可能允许缺失或不适用于该样本"},
    "constant_field": {"applies_to": _COMMON, "trigger": "非空唯一值不超过 1", "sample": "至少 1 个非空值", "location": "列名", "risk": "处理组、批次或常量设计字段本来就可能不变"},
    "mixed_numeric_text": {"applies_to": "包含可解析数值的混合列", "trigger": "同列非空值中同时出现可解析数值与文本", "sample": "至少 1 个数值和 1 个文本值", "location": "列名；命中单元格另行定位", "risk": "单位、低于检测限标记或类别编码可能合法"},
    "non_numeric_cell": {"applies_to": "被设置为数值检查的字段", "trigger": "非空值无法按当前解析规则转为数值", "sample": "逐单元格", "location": "工作表、行、列对应单元格", "risk": "文字单位、特殊缺失码或分组标签可能是预期值"},
    "iqr_outlier": {"applies_to": "数值占比足够的连续/计量列", "trigger": "数值超出组内 1.5×IQR 范围", "sample": "至少 8 个有效值/组", "location": "具体行和字段单元格", "risk": "探索性统计；真实生物差异、偏态分布和小样本均可能触发"},
    "iqr_insufficient": {"applies_to": "配置为数值检查的分组列", "trigger": "组内有效样本不足", "sample": "不足 8 个有效值/组", "location": "字段列", "risk": "这是覆盖提示，不是异常判定"},
    "out_of_range": {"applies_to": "用户在 checks_json 中提供上下限的字段", "trigger": "数值低于 min 或高于 max", "sample": "逐单元格", "location": "具体单元格", "risk": "范围取决于物种、单位、时期和实验方案"},
    "logic_conflict": {"applies_to": "用户提供关系规则的成对字段", "trigger": "单元格值违反显式关系条件", "sample": "逐行", "location": "参与关系的单元格", "risk": "字段方向、单位或缺失值处理需由用户确认"},
    "duplicate_key": {"applies_to": "用户声明的主键字段", "trigger": "同一非空键出现多次", "sample": "全列/全表", "location": "重复键行和字段", "risk": "重复测量或长表结构可能允许同一键重复"},
    "duplicate_row": {"applies_to": _COMMON, "trigger": "完整数据行完全相同", "sample": "至少 2 行", "location": "重复行", "risk": "技术重复、共享对照或合法重复记录可能相同"},
    "multi_header_suspected": {"applies_to": "表头区域", "trigger": "数据区出现与表头形态相似的行", "sample": "扫描表格行", "location": "疑似重复表头行", "risk": "分段表格和分组标题也可能符合该形态"},
    "optional_column": {"applies_to": "备注/说明类自由文本列", "trigger": "列名属于备注类且未配置任何规则", "sample": "整列扫描", "location": "列名", "risk": "该列可能本应必填；已被 optional_fields 或其它规则覆盖时不会触发"},
    "identical_columns": {"applies_to": "可比较的数值列", "trigger": "两列在有效位置完全相同", "sample": "按规则要求的有效行数", "location": "列对及相关单元格", "risk": "镜像测量、派生列或共享变量可能有意相同"},
    "constant_offset_columns": {"applies_to": "可比较数值列对", "trigger": "两列差值保持恒定", "sample": "多个共同有效行", "location": "列对", "risk": "单位换算、基线校正可能导致恒定偏移"},
    "constant_ratio_columns": {"applies_to": "可比较数值列对", "trigger": "两列比值保持恒定", "sample": "多个非零共同有效行", "location": "列对", "risk": "比例换算或已知派生字段可能合法"},
    "exact_linear_columns": {"applies_to": "可比较数值列组", "trigger": "列间呈近似精确线性关系", "sample": "多个共同有效行", "location": "列组", "risk": "公式派生、浓度换算或结构性相关会触发"},
    "decimal_ending_concentration": {"applies_to": "精度足够的数值列", "trigger": "末位小数分布集中", "sample": "按列内有效值和精度阈值", "location": "列名", "risk": "仪器精度、舍入规则、固定分母会形成集中末位"},
    "unequal_group_size": {"applies_to": "用户确认的分组字段", "trigger": "各组样本数量不相等", "sample": "至少两个组", "location": "组别字段", "risk": "不平衡设计可能是预先设定"},
    "unequal_replicates": {"applies_to": "用户确认的分组/重复字段", "trigger": "组内重复编号数量不相等", "sample": "至少两个组", "location": "重复字段和组别", "risk": "缺失样本、筛除规则或不平衡设计可能合理"},
    "integer_mean_inconsistent": {"applies_to": "明确为整数观测汇总的均值和样本量", "trigger": "均值×n 与整数和相差超过舍入容差", "sample": "n 为大于 1 的整数", "location": "均值字段和行", "risk": "加权均值、变换值、舍入或 n 定义会触发"},
    "integer_summary_inconsistent": {"applies_to": "明确整数观测的均值、SD、n", "trigger": "反推平方和不接近整数", "sample": "n>1 且 SD 类型已确认", "location": "SD 字段和行", "risk": "舍入、加权统计或 SD 口径不同会触发"},
    "design_checks_skipped": {"applies_to": "分组平衡检查", "trigger": "未确认实验分组字段", "sample": "不适用", "location": "整张表", "risk": "这是未执行范围说明，不是异常"},
    "invalid_group_mapping": {"applies_to": "模型/用户给出的分组字段映射", "trigger": "映射字段不存在于表头", "sample": "表头核对", "location": "字段名和整表", "risk": "合并表头、重名列或映射歧义可能造成误判"},
    "invalid_value_mapping": {"applies_to": "模型/用户给出的测量字段映射", "trigger": "映射字段不存在于表头", "sample": "表头核对", "location": "字段名和整表", "risk": "合并表头、重名列或映射歧义可能造成误判"},
    "grim_sd_skipped": {"applies_to": "整数观测的 SD 一致性检查", "trigger": "未确认样本 SD 或总体 SD", "sample": "不适用", "location": "相关统计字段", "risk": "这是未执行范围说明，不是异常"},
    "findings_truncated": {"applies_to": "本次发现清单", "trigger": "发现条数超过单次上限", "sample": "不适用", "location": "整张表", "risk": "清单不完整，未列出的部分不能判断为无异常"},
}


def describe_rule(check: str) -> dict[str, str]:
    """Return display-safe rule metadata; unknown future rules remain visible."""
    key = str(check or "").strip()
    return {"check": key, **RULES.get(key, {
        "applies_to": "请人工确认", "trigger": "规则执行器命中", "sample": "规则特定",
        "location": "以问题清单定位为准", "risk": "信号仅供复核，不代表数据错误",
    })}


def rule_manifest(
    checks: Iterable[str] | None = None,
    *,
    executed: Iterable[str] | None = None,
    skipped: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Describe the rule catalogue with actual execution status.

    ``available`` means the service supports the rule. ``executed`` means it
    ran for this audit. ``skipped`` means configuration or data prerequisites
    were absent; the workbook records the reason instead of implying coverage.
    """
    keys = sorted(set(str(item or "").strip() for item in (checks or RULES)) - {""})
    executed_keys = {str(item or "").strip() for item in (executed or ())}
    skipped_reasons = {
        str(key or "").strip(): str(reason or "").strip()
        for key, reason in (skipped or {}).items()
    }
    rows = []
    for key in keys:
        row = describe_rule(key)
        if key in executed_keys:
            row.update(status="executed", reason="")
        elif key in skipped_reasons:
            row.update(status="skipped", reason=skipped_reasons[key])
        else:
            row.update(status="available", reason="本次没有满足执行的触发前提")
        rows.append(row)
    return rows
