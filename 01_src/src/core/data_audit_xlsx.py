# -*- coding: utf-8 -*-
"""把表格审查结果落成「可直接定位问题」的标注 Excel。

设计原则（与需求逐条对应）：
- 原始数据只读：本模块只读取上传字节，写到一个**新**工作簿，绝不回写原文件。
- 不篡改单元格：高亮用填充色 + 红色边框，问题写在 Excel 批注里；空值只着色，
  不写入「缺失」字符串，避免把数值列变成文本。
- 可追溯：每个问题都进「问题清单」，记录工作表、单元格、类型、原值、规则、
  严重程度与建议动作；一条规则一行，同一单元格可有多行。
- 不伪造精度：只有能定位到具体单元格的发现才着色；只能定位到整列/整表的
  统计信号只写进问题清单，不整列染红。
"""

from __future__ import annotations

import io as _io
import re
from typing import Any, Iterable

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Border, PatternFill, Side
from openpyxl.utils import get_column_letter

_ISSUE_SHEET = "问题清单"
_OVERVIEW_SHEET = "审查概览"
_FILL = PatternFill("solid", fgColor="FDE2E1")
_SIDE = Side(style="thin", color="E06666")
_BORDER = Border(left=_SIDE, right=_SIDE, top=_SIDE, bottom=_SIDE)

# 严重程度排序键：阻断 > 需核查 > 提示（未知值排最后，不吞掉任何条目）
_SEVERITY_RANK = {"阻断": 0, "错误": 0, "需核查": 1, "提示": 2, "正常": 3}
_ANNOTATION_SEVERITIES = {"ok": "正常", "review": "需核查", "error": "错误"}
_ANNOTATION_STYLES = {
    "正常": ("E7F4EA", "1F6B33"),
    "需核查": ("FDF1D6", "8A5A00"),
}


def _unique_sheet_title(wb, desired: str) -> str:
    """Choose a workbook metadata title without replacing an uploaded sheet."""
    base = re.sub(r"[\\/*?:\[\]]", "_", str(desired or "审查信息")).strip() or "审查信息"
    base = base[:31]
    existing = {str(name).casefold() for name in wb.sheetnames}
    if base.casefold() not in existing:
        return base
    for index in range(2, 1000):
        suffix = f"_{index}"
        candidate = base[:31 - len(suffix)] + suffix
        if candidate.casefold() not in existing:
            return candidate
    raise ValueError("工作簿中可用的审查工作表名称已耗尽")


def _severity_rank(item: dict) -> int:
    """问题清单排序：先按严重程度，再保持原有工作表/行顺序（sorted 是稳定排序）。"""
    return _SEVERITY_RANK.get(str(item.get("severity") or "").strip(), 3)


# 检测类型 → (中文名, 严重程度, 建议核查动作)
_KINDS: dict[str, tuple[str, str, str]] = {
    "missing_cell": ("缺失", "需核查", "核对原始记录或实验台账，确认该值确实未测"),
    "missing_values": ("缺失", "需核查", "确认该列是否允许缺失，补齐或说明"),
    "non_numeric_cell": ("类型错误", "需核查", "确认是单位/符号/文本类别，还是录入错误"),
    "mixed_numeric_text": ("类型错误", "需核查", "分离单位或文本标记，保持数值列纯净"),
    "duplicate_row": ("重复", "需核查", "确认是重复录入还是合法的重复测量"),
    "duplicate_key": ("重复", "需核查", "确认样本/主键是否唯一，或改用分组编号"),
    "out_of_range": ("越界", "需核查", "与实验设计的合理范围比对后修正"),
    "logic_conflict": ("逻辑冲突", "需核查", "核对两列来源与计算关系"),
    "constant_field": ("常量列", "提示", "确认该字段是否有信息量"),
    "identical_columns": ("列重复", "需核查", "确认是否复制粘贴造成的重复列"),
    "constant_offset_columns": ("列关系", "需核查", "确认是否为换算列"),
    "constant_ratio_columns": ("列关系", "需核查", "确认是否为比例换算列"),
    "exact_linear_columns": ("列关系", "需核查", "确认是否为公式派生列"),
    "decimal_ending_concentration": ("末位集中", "提示", "确认是否为舍入或固定分母造成"),
    "unequal_group_size": ("分组不均", "提示", "确认是否按设计"),
    "unequal_replicates": ("重复数不一", "提示", "核对重复定义"),
    "integer_mean_inconsistent": ("均值不一致", "需核查", "核对数据粒度、舍入与 n 定义"),
    "integer_summary_inconsistent": ("统计量不一致", "需核查", "核对 SD 定义与舍入"),
    "invalid_group_mapping": ("字段未匹配", "提示", "修正分组字段名后重跑"),
    "invalid_value_mapping": ("字段未匹配", "提示", "修正测量字段名后重跑"),
    "design_checks_skipped": ("未做分组检查", "提示", "如需分组检查，请说明分组列"),
    "grim_sd_skipped": ("未做 SD 检查", "提示", "确认报告的是样本 SD 还是总体 SD"),
    "empty_table": ("空表", "阻断", "确认文件是否选错或表头行是否正确"),
    "empty_column": ("空列", "提示", "整列无数据，通常是公式列或预留空白列；如该列应有数据请核对导出方式"),
    "missing_row": ("缺行", "需核查", "确认该行是否被合并单元格或公式行占用"),
    "iqr_insufficient": ("样本不足", "提示", "组内样本不足 8，不做离群推断；如需判定请补充重复测量"),
    "iqr_outlier": ("离群值", "需核查", "对照原始记录确认该值是否为录入错误或真实生物学差异"),
    "mixed_numeric_text": ("类型混杂", "需核查", "统一该列的数值格式，或把文本说明移到备注列"),
    # 按行标注路径的结论，不是规则命中：措辞必须让读者看出这是模型意见。
    "row_annotation": ("按行标注", "需核查", "该结论由模型给出，请对照原始记录与实验设计人工确认"),
}


def _kind(check: str) -> tuple[str, str, str]:
    return _KINDS.get(check, (check or "其他", "提示", "人工复核"))


def _header_map(ws, header_row: int) -> dict[str, int]:
    """表头文字 → 列号（1 基）。兼容 pandas 重名后缀 ``.1``。"""
    mapping: dict[str, int] = {}
    for cell in ws[header_row]:
        if cell.value is None:
            continue
        name = str(cell.value).strip()
        if not name:
            continue
        mapping.setdefault(name, cell.column)
    return mapping


def _resolve_column(field: str, header: dict[str, int]) -> int | None:
    text = str(field or "").strip()
    if not text:
        return None
    if text in header:
        return header[text]
    base = text.rsplit(" ↔ ", 1)[0]
    if base in header:
        return header[base]
    import re
    stripped = re.sub(r"\.\d+$", "", base)
    return header.get(stripped)


def _collect_sheet_issues(ws, header_row: int, findings: Iterable[dict[str, Any]],
                          ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """把一张表上的 findings 解析成行记录与「单元格 → 问题」映射（不改样式）。"""
    header = _header_map(ws, header_row)
    cell_issues: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    for finding in findings:
        check = str(finding.get("check") or "")
        label, severity, action = _kind(check)
        if check == "row_annotation":
            severity = _ANNOTATION_SEVERITIES.get(str(finding.get("level") or ""), severity)
        field = str(finding.get("field") or "")
        excel_row = finding.get("row")
        raw_column_index = finding.get("column_index")
        col_idx = (
            int(raw_column_index)
            if type(raw_column_index) is int
            and 1 <= int(raw_column_index) <= int(ws.max_column or 0)
            else None
        )
        if col_idx is None:
            col_idx = _resolve_column(field, header)
        if not col_idx:
            # pandas 把无名列命名为「未命名列6」/「Unnamed: 5」，据此还原 Excel 列号，
            # 否则空列问题只能显示“（整表）”，用户没法定位。
            match = re.fullmatch(r"\s*(?:未命名列|Unnamed:\s*)(\d+)\s*", field)
            if match:
                col_idx = int(match.group(1))
        ref = ""
        row_refs: list[str] = []
        if isinstance(excel_row, int) and excel_row > 0 and col_idx:
            ref = f"{get_column_letter(col_idx)}{excel_row}"
        elif col_idx:
            ref = f"{get_column_letter(col_idx)}列"
        elif isinstance(excel_row, int) and excel_row > 0:
            # 行级问题（如完全重复行）没有列字段：把该行所有数据列都标出来，
            # 否则用户只看到问题清单，却无法在原始单元格定位。
            data_cols = max(header.values()) if header else ws.max_column
            row_refs = [f"{get_column_letter(c)}{excel_row}"
                        for c in range(1, int(data_cols) + 1)]
        value = ""
        if ref and "!" not in ref and not ref.endswith("列"):
            value = ws[ref].value
        rows.append({
            "sheet": ws.title,
            "cell": ref or (f"第 {excel_row} 行（整行）" if row_refs else "（整表）"),
            "row": excel_row if isinstance(excel_row, int) else "",
            "column": field,
            "column_index": col_idx or "",
            "kind": label,
            "severity": severity,
            "value": "" if value is None else str(value)[:120],
            "rule": check,
            "detail": str(finding.get("detail") or "")[:500],
            "action": action,
        })
        if ref and not ref.endswith("列"):
            cell_issues.setdefault(ref, []).append(rows[-1])
        elif row_refs:
            for row_ref in row_refs:
                cell_issues.setdefault(row_ref, []).append(rows[-1])
    return rows, cell_issues


def _apply_cell_marks(ws, cell_issues: dict[str, list[dict[str, Any]]]) -> None:
    """合并区域的发现汇总到可写的左上格；问题清单仍保留原始坐标。"""
    marks: dict[str, list[dict[str, Any]]] = {}
    for ref, issues in cell_issues.items():
        cell = ws[ref]
        if isinstance(cell, MergedCell):
            for region in ws.merged_cells.ranges:
                if ref in region:
                    cell = ws.cell(region.min_row, region.min_col)
                    break
        marks.setdefault(cell.coordinate, []).extend(issues)
    for ref, issues in marks.items():
        cell = ws[ref]
        # Preserve the three manual verdicts; an explicitly normal row must
        # not become a red warning merely because it shares the audit writer.
        manual_only = all(item["rule"] == "row_annotation" for item in issues)
        severity = min(issues, key=_severity_rank)["severity"]
        colors = _ANNOTATION_STYLES.get(severity) if manual_only else None
        if colors:
            cell.fill = PatternFill("solid", fgColor=colors[0])
            side = Side(style="thin", color=colors[1])
            cell.border = Border(left=side, right=side, top=side, bottom=side)
        else:
            cell.fill = _FILL
            cell.border = _BORDER
        text = "\n".join(f"· {item['kind']}：{item['detail']}" for item in issues[:6])
        if len(issues) > 6:
            text += f"\n另有 {len(issues) - 6} 条，详见问题清单。"
        previous = cell.comment.text + "\n\n" if cell.comment else ""
        caveat = ("以上为模型按行标注结论，请结合原始记录人工确认。"
                  if manual_only else "以上为待核查信号，不代表数据错误。")
        note = Comment(previous + f"{text}\n\n{caveat}", "CitrusSchalor")
        note.width, note.height = 320, 160
        cell.comment = note


def annotate_workbook_multi(data: bytes, *, targets: Iterable[dict[str, Any]],
                            notes: Iterable[str] = (),
                            rule_catalog: Iterable[dict[str, Any]] = (),
                            source_name: str = "", source_sha256: str = "") -> tuple[bytes, dict[str, Any]]:
    """在一份副本里标注**多个工作表**，输出一个综合标注工作簿。

    targets 每项：{sheet_name, header_row, findings, row_count, column_count}。
    为什么必须一次输出：同一份多 Sheet 源文件若按表逐份生成，用户会拿到若干个
    名字相近的结果文件，无法判断哪份是最终产物。
    """
    wb = load_workbook(_io.BytesIO(data))
    all_rows: list[dict[str, Any]] = []
    per_sheet: list[dict[str, Any]] = []
    total_cells = 0
    for target in targets:
        name = str(target.get("sheet_name") or "")
        if name not in wb.sheetnames:
            raise ValueError(f"标注目标工作表不存在：{name}")
        ws = wb[name]
        rows, cell_issues = _collect_sheet_issues(
            ws, int(target.get("header_row") or 1), target.get("findings") or [])
        _apply_cell_marks(ws, cell_issues)
        all_rows.extend(rows)
        total_cells += len(cell_issues)
        per_sheet.append({
            "sheet": ws.title,
            "rows": int(target.get("row_count") or 0),
            "columns": int(target.get("column_count") or 0),
            "issues": len(rows),
            "cells": len(cell_issues),
            "coverage": dict(target.get("coverage") or {}),
        })

    issue_ws = wb.create_sheet(_unique_sheet_title(wb, _ISSUE_SHEET))
    issue_ws.append(["序号", "工作表", "单元格", "行", "列", "列索引", "问题类型",
                     "严重程度", "原值", "检测规则", "说明", "建议核查动作"])
    # 严重程度优先：阻断类问题不该排在「提示」后面，否则管理员会先看到无关项。
    for index, item in enumerate(sorted(all_rows, key=_severity_rank), start=1):
        issue_ws.append([index, item["sheet"], item["cell"], item["row"], item["column"],
                         item["column_index"], item["kind"], item["severity"],
                         item["value"], item["rule"], item["detail"], item["action"]])
    for column, width in zip("ABCDEFGHIJKL", (6, 14, 14, 8, 18, 10, 14, 10, 16, 22, 60, 34)):
        issue_ws.column_dimensions[column].width = width
    issue_ws.freeze_panes = "A2"

    overview = wb.create_sheet(_unique_sheet_title(wb, _OVERVIEW_SHEET))
    counts: dict[str, int] = {}
    for item in all_rows:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    overview.append(["项目", "值"])
    overview.append(["审查工作表数", len(per_sheet)])
    overview.append(["审查工作表", "、".join(item["sheet"] for item in per_sheet)])
    if source_name:
        overview.append(["原始文件", str(source_name)])
    if source_sha256:
        overview.append(["原始文件 SHA-256", str(source_sha256)])
    overview.append(["数据行数（合计）", sum(item["rows"] for item in per_sheet)])
    if any(item["rule"] == "row_annotation" for item in all_rows):
        overview.append(["按行标注条目数", sum(item["rule"] == "row_annotation" for item in all_rows)])
    overview.append(["待核查问题数", sum(item["severity"] != "正常" for item in all_rows)])
    overview.append(["涉及单元格数", total_cells])
    overview.append(["问题类型分布", "；".join(f"{k} {v}" for k, v in sorted(counts.items())) or "无"])
    overview.append(["说明", "检测命中属于待核查信号，不代表研究数据造假；原始文件未被修改。"])
    for item in per_sheet:
        coverage = item.get("coverage") or {}
        overview.append([f"工作表 {item['sheet']}",
                         f"{item['rows']} 行 / {item['columns']} 列 / {item['issues']} 条问题 / {item['cells']} 个标注单元格"])
        overview.append([f"工作表 {item['sheet']} 覆盖范围",
                         f"输入 {coverage.get('input_rows', item['rows'])} 行、"
                         f"处理 {coverage.get('processed_rows', item['rows'])} 行；"
                         f"输入 {coverage.get('input_columns', item['columns'])} 列、"
                         f"处理 {coverage.get('processed_columns', item['columns'])} 列"])
        if coverage.get("truncation_reasons"):
            overview.append([f"工作表 {item['sheet']} 截断原因",
                             "；".join(str(x) for x in coverage["truncation_reasons"])])
        if coverage.get("unscanned_columns"):
            overview.append([f"工作表 {item['sheet']} 未覆盖列",
                             "；".join(str(x) for x in coverage["unscanned_columns"][:200])])
    for note in notes:
        overview.append(["限制", str(note)])
    overview.column_dimensions["A"].width = 22
    overview.column_dimensions["B"].width = 90

    rules = list(rule_catalog)
    if rules:
        rule_ws = wb.create_sheet(_unique_sheet_title(wb, "审查规则"))
        rule_ws.append(["规则代码", "执行状态", "未执行原因", "适用数据", "触发条件",
                        "样本要求", "定位方法", "误报风险"])
        for rule in rules:
            rule_ws.append([str(rule.get("check") or ""),
                            str(rule.get("status") or "available"),
                            str(rule.get("reason") or ""),
                            str(rule.get("applies_to") or ""),
                            str(rule.get("trigger") or ""),
                            str(rule.get("sample") or ""),
                            str(rule.get("location") or ""),
                            str(rule.get("risk") or "")])
        for column, width in zip("ABCDEFGH", (30, 12, 42, 42, 48, 28, 34, 54)):
            rule_ws.column_dimensions[column].width = width
        rule_ws.freeze_panes = "A2"
        rule_ws.auto_filter.ref = rule_ws.dimensions

    buffer = _io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue(), {
        "issues": len(all_rows),
        "cells": total_cells,
        "kinds": counts,
        "sheets": per_sheet,
        "sheet": per_sheet[0]["sheet"] if per_sheet else "",
        "rule_count": len(rules),
        "source_sha256": str(source_sha256 or ""),
    }


def annotate_workbook(data: bytes, *, sheet_name: str, header_row: int,
                      findings: Iterable[dict[str, Any]],
                      row_count: int = 0, column_count: int = 0,
                      notes: Iterable[str] = ()) -> tuple[bytes, dict[str, Any]]:
    """单表标注入口（CSV 与只用一张表的老调用方），内部走多表实现。"""
    return annotate_workbook_multi(data, targets=[{
        "sheet_name": sheet_name, "header_row": header_row, "findings": findings,
        "row_count": row_count, "column_count": column_count,
    }], notes=notes)
