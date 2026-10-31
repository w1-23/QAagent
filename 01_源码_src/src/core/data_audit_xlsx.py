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
from openpyxl.styles import Border, PatternFill, Side
from openpyxl.utils import get_column_letter

_ISSUE_SHEET = "问题清单"
_OVERVIEW_SHEET = "审查概览"
_FILL = PatternFill("solid", fgColor="FDE2E1")
_SIDE = Side(style="thin", color="E06666")
_BORDER = Border(left=_SIDE, right=_SIDE, top=_SIDE, bottom=_SIDE)

# 检测类型 → (中文名, 严重程度, 建议核查动作)
_KINDS: dict[str, tuple[str, str, str]] = {
    "missing_cell": ("缺失", "需核查", "核对原始记录或实验台账，确认该值确实未测"),
    "missing_values": ("缺失", "需核查", "确认该列是否允许缺失，补齐或说明"),
    "non_numeric_cell": ("类型错误", "需核查", "确认是单位/符号/文本类别，还是录入错误"),
    "mixed_numeric_text": ("类型错误", "需核查", "分离单位或文本标记，保持数值列纯净"),
    "duplicate_row": ("重复", "需核查", "确认是重复录入还是合法的重复测量"),
    "duplicate_key": ("重复", "需核查", "确认样本/主键是否唯一，或改用分组编号"),
    "iqr_outlier": ("疑似离群", "需核查", "回到原始仪器记录核对，不要直接删除"),
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


def annotate_workbook(data: bytes, *, sheet_name: str, header_row: int,
                      findings: Iterable[dict[str, Any]],
                      row_count: int = 0, column_count: int = 0,
                      notes: Iterable[str] = ()) -> tuple[bytes, dict[str, Any]]:
    """在原始工作簿副本上标注问题单元格，返回 (xlsx 字节, 统计信息)。"""
    wb = load_workbook(_io.BytesIO(data))
    ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb.active
    header = _header_map(ws, header_row)

    cell_issues: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    for finding in findings:
        check = str(finding.get("check") or "")
        label, severity, action = _kind(check)
        field = str(finding.get("field") or "")
        excel_row = finding.get("row")
        col_idx = _resolve_column(field, header)
        if not col_idx:
            # pandas 把无名列命名为「未命名列6」/「Unnamed: 5」，据此还原 Excel 列号，
            # 否则空列问题只能显示“（整表）”，用户没法定位。
            match = re.fullmatch(r"\s*(?:未命名列|Unnamed:\s*)(\d+)\s*", field)
            if match:
                col_idx = int(match.group(1))
        ref = ""
        if isinstance(excel_row, int) and excel_row > 0 and col_idx:
            ref = f"{get_column_letter(col_idx)}{excel_row}"
        elif col_idx:
            ref = f"{get_column_letter(col_idx)}列"
        value = ""
        if ref and "!" not in ref and not ref.endswith("列"):
            value = ws[ref].value
        rows.append({
            "sheet": ws.title,
            "cell": ref or "（整表）",
            "row": excel_row if isinstance(excel_row, int) else "",
            "column": field,
            "kind": label,
            "severity": severity,
            "value": "" if value is None else str(value)[:120],
            "rule": check,
            "detail": str(finding.get("detail") or "")[:500],
            "action": action,
        })
        if ref and not ref.endswith("列"):
            cell_issues.setdefault(ref, []).append(rows[-1])

    for ref, issues in cell_issues.items():
        cell = ws[ref]
        cell.fill = _FILL
        cell.border = _BORDER
        text = "\n".join(f"· {item['kind']}：{item['detail']}" for item in issues[:6])
        note = Comment(f"{text}\n\n以上为待核查信号，不代表数据错误。", "CitrusScholar")
        note.width, note.height = 320, 160
        cell.comment = note

    issue_ws = wb.create_sheet(_ISSUE_SHEET)
    issue_ws.append(["序号", "工作表", "单元格", "行", "列", "问题类型", "严重程度",
                     "原值", "检测规则", "说明", "建议核查动作"])
    for index, item in enumerate(rows, start=1):
        issue_ws.append([index, item["sheet"], item["cell"], item["row"], item["column"],
                         item["kind"], item["severity"], item["value"], item["rule"],
                         item["detail"], item["action"]])
    for column, width in zip("ABCDEFGHIJK", (6, 14, 14, 8, 18, 14, 10, 16, 22, 60, 34)):
        issue_ws.column_dimensions[column].width = width
    issue_ws.freeze_panes = "A2"

    overview = wb.create_sheet(_OVERVIEW_SHEET)
    counts: dict[str, int] = {}
    for item in rows:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    overview.append(["项目", "值"])
    overview.append(["工作表", ws.title])
    overview.append(["数据行数", int(row_count)])
    overview.append(["数据列数", int(column_count)])
    overview.append(["待核查问题数", len(rows)])
    overview.append(["涉及单元格数", len(cell_issues)])
    overview.append(["问题类型分布", "；".join(f"{k} {v}" for k, v in sorted(counts.items())) or "无"])
    overview.append(["说明", "检测命中属于待核查信号，不代表研究数据造假；原始文件未被修改。"])
    for note in notes:
        overview.append(["限制", str(note)])
    overview.column_dimensions["A"].width = 18
    overview.column_dimensions["B"].width = 90

    buffer = _io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue(), {
        "issues": len(rows), "cells": len(cell_issues),
        "kinds": counts, "sheet": ws.title,
        "sheets": [name for name in wb.sheetnames],
    }
