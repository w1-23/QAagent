# -*- coding: utf-8 -*-
"""把表格审查结果落成「可直接定位问题」的标注 Excel。

设计原则（与需求逐条对应）：
- 原始数据只读：本模块只读取上传字节，写到一个**新**工作簿，绝不回写原文件。
- 不篡改单元格：高亮用填充色 + 红色边框，问题写在 Excel 批注里；空值只着色，
  不写入「缺失」字符串，避免把数值列变成文本。
- 默认不增加工作表：问题清单、审查概览、审查规则分别通过显式开关导出。
- 兼容旧的 findings/targets 与人工按行意见；新调用方优先使用导入的
  export_workbook(AuditSnapshot)，从已保存的审查快照导出，不重复扫描。
- 不伪造精度：只有能定位到具体单元格的发现才着色；只能定位到整列/整表的
  统计信号只写进问题清单，不整列染红。
"""

from __future__ import annotations

import io as _io
import json
import re
from typing import Any, Iterable

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Border, PatternFill, Side
from openpyxl.utils import get_column_letter

from citrus_audit.api import AuditSnapshot, export_workbook
from citrus_audit.workbook import (
    _kind, _unique_sheet_title, _restore_original_cells, _data_signature,
)

_ISSUE_SHEET = "问题清单"
_OVERVIEW_SHEET = "审查概览"
_FILL = PatternFill("solid", fgColor="FDE2E1")
_SIDE = Side(style="thin", color="E06666")
_BORDER = Border(left=_SIDE, right=_SIDE, top=_SIDE, bottom=_SIDE)

# 严重程度排序键：阻断 > 需核查 > 提示（未知值排最后，不吞掉任何条目）
_SEVERITY_RANK = {"阻断": 0, "错误": 0, "警告": 1, "需核查": 1, "提示": 2, "正常": 3}
_FINDING_SEVERITIES = {"error": "错误", "warning": "警告", "review": "需核查", "info": "提示", "ok": "正常"}
_ANNOTATION_SEVERITIES = {"ok": "正常", "review": "需核查", "error": "错误"}
_ANNOTATION_STYLES = {
    "正常": ("E7F4EA", "1F6B33"),
    "需核查": ("FDF1D6", "8A5A00"),
}


def _severity_rank(item: dict) -> int:
    """问题清单排序：先按严重程度，再保持原有工作表/行顺序（sorted 是稳定排序）。"""
    return _SEVERITY_RANK.get(str(item.get("severity") or "").strip(), 3)


def _header_map(ws, header_row: int) -> dict[str, int]:
    """表头文字 → 列号（1 基）。兼容 pandas 重名后缀 ``.1``。"""
    mapping: dict[str, int] = {}
    if not 1 <= header_row <= ws.max_row:
        return mapping
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
    height, width = ws.max_row, ws.max_column
    header = _header_map(ws, header_row)
    cell_issues: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    for finding in findings:
        check = str(finding.get("check") or "")
        label, severity, action = _kind(check)
        # Legacy callers may omit severity; a real finding always owns it.
        if finding.get("severity"):
            severity = _FINDING_SEVERITIES.get(str(finding["severity"]), str(finding["severity"]))
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
        # Keep supplied coordinates in the report, but never read or mark beyond
        # the uploaded data region: ws[ref] itself creates a new empty cell.
        row_in_source = type(excel_row) is int and header_row < excel_row <= height
        cell_in_source = row_in_source and col_idx is not None and 1 <= col_idx <= width
        evidence = finding.get('evidence') or {}
        evidence_refs = []
        for cell in evidence.get('cells', []) if isinstance(evidence, dict) else []:
            if (isinstance(cell, dict) and type(cell.get('row')) is int and type(cell.get('column_index')) is int
                    and header_row < cell['row'] <= height and 1 <= cell['column_index'] <= width):
                evidence_refs.append(f"{get_column_letter(cell['column_index'])}{cell['row']}")
        evidence_refs = list(dict.fromkeys(evidence_refs))
        value = ""
        if cell_in_source:
            value = ws[ref].value
        rows.append({
            "sheet": ws.title,
            "cell": ','.join(evidence_refs[:20]) + ('…' if len(evidence_refs) > 20 else '') if evidence_refs else
                    ref or (f"第 {excel_row} 行（整行）" if row_refs else "（整表）"),
            "evidence": json.dumps(evidence, ensure_ascii=False, default=str),
            "layer": 'manual_or_model_annotation' if check == 'row_annotation' else 'deterministic',
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
        if evidence_refs:
            for cell_ref in evidence_refs:
                cell_issues.setdefault(cell_ref, []).append(rows[-1])
        elif cell_in_source:
            cell_issues.setdefault(ref, []).append(rows[-1])
        elif row_refs and row_in_source:
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
        text = "\n".join(f"· [{item['severity']}] {item['kind']}：{item['detail']}" for item in issues[:6])
        if len(issues) > 6:
            text += f"\n另有 {len(issues) - 6} 条，完整发现见审查结果。"
        previous = cell.comment.text + "\n\n" if cell.comment else ""
        caveat = ("以上为人工/模型按行注释，请结合原始记录确认。" if manual_only else
                  "错误表示违反显式数据规则；需核查/提示为复核线索。检测结果不代表科研不端，原始值未修改。")
        note = Comment(previous + f"{text}\n\n{caveat}", "CitrusScholar")
        note.width, note.height = 320, 160
        cell.comment = note


def annotate_workbook_multi(data: bytes, *, targets: Iterable[dict[str, Any]],
                            notes: Iterable[str] = (),
                            rule_catalog: Iterable[dict[str, Any]] = (),
                            source_name: str = "", source_sha256: str = "",
                            issue_list: bool = False, overview: bool = False,
                            rules: bool = False) -> tuple[bytes, dict[str, Any]]:
    """在一份副本里标注**多个工作表**，输出一个综合标注工作簿。

    targets 每项：{sheet_name, header_row, findings, row_count, column_count}。
    多表只输出一个副本。所有附加工作表均须显式开启；rule_catalog 与 notes
    仅携带审查元数据，不会隐式开启附表。调用时提供的 findings 不会重新计算。
    """
    import hashlib
    if any(type(flag) is not bool for flag in (issue_list, overview, rules)):
        raise ValueError("Export switches must be boolean")
    actual_sha256 = hashlib.sha256(data).hexdigest()
    # Legacy CSV/PDF/etc callers pass a converted workbook plus the original
    # source's provenance hash. Only original-workbook bytes can be checked here;
    # the snapshot export API validates original bytes for every source format.
    original_workbook = not source_name or str(source_name).lower().endswith(('.xlsx', '.xlsm'))
    if original_workbook and source_sha256 and source_sha256 != actual_sha256:
        raise ValueError("Reviewed source hash mismatch")
    keep_vba = str(source_name).lower().endswith('.xlsm')
    wb = load_workbook(_io.BytesIO(data), keep_vba=keep_vba)
    bounds = {ws.title: (ws.max_row, ws.max_column) for ws in wb.worksheets}
    if sum(height * width for height, width in bounds.values()) > 2_000_000:
        raise ValueError("Workbook export exceeds 2000000-cell verification budget")
    before = _data_signature(wb, bounds)
    original_sheets = list(wb.sheetnames)
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

    if issue_list:
        issue_ws = wb.create_sheet(_unique_sheet_title(wb, _ISSUE_SHEET))
        issue_ws.append(["序号", "工作表", "单元格", "行", "列", "列索引", "问题类型",
                         "严重程度", "原值", "检测规则", "说明", "建议核查动作", "结果层", "计算/定位证据（长内容需读完整结果）"])
        # 严重程度优先：阻断类问题不该排在「提示」后面，否则管理员会先看到无关项。
        for index, item in enumerate(sorted(all_rows, key=_severity_rank), start=1):
            issue_ws.append([index, item["sheet"], item["cell"], item["row"], item["column"],
                             item["column_index"], item["kind"], item["severity"],
                             item["value"], item["rule"], item["detail"], item["action"], item['layer'],
                             item['evidence'][:32000] + ('…（已截断；完整证据见审查结果）' if len(item['evidence']) > 32000 else '')])
        for column, width in zip("ABCDEFGHIJKL", (6, 14, 14, 8, 18, 10, 14, 10, 16, 22, 60, 34)):
            issue_ws.column_dimensions[column].width = width
        issue_ws.freeze_panes = "A2"

    counts: dict[str, int] = {}
    for item in all_rows:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    if overview:
        overview = wb.create_sheet(_unique_sheet_title(wb, _OVERVIEW_SHEET))
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

    catalog = list(rule_catalog)
    if rules and catalog:
        rule_ws = wb.create_sheet(_unique_sheet_title(wb, "审查规则"))
        rule_ws.append(["规则代码", "执行状态", "未执行原因", "适用数据", "触发条件",
                        "样本要求", "定位方法", "误报风险", "工作表", "方法版本", "实际检查数", "跳过数", "范围/阈值/截断凭证"])
        for rule in catalog:
            rule_ws.append([str(rule.get("check") or ""),
                            str(rule.get("status") or "available"),
                            str(rule.get("reason") or ""),
                            str(rule.get("applies_to") or ""),
                            str(rule.get("trigger") or ""),
                            str(rule.get("sample") or ""),
                            str(rule.get("location") or ""),
                            str(rule.get("risk") or ""), str(rule.get('sheet') or ''),
                            str(rule.get('method_version') or ''), rule.get('tested', 0), rule.get('skipped', 0),
                            json.dumps(rule, ensure_ascii=False, default=str)[:32000]])
        for column, width in zip("ABCDEFGH", (30, 12, 42, 42, 48, 28, 34, 54)):
            rule_ws.column_dimensions[column].width = width
        rule_ws.freeze_panes = "A2"
        rule_ws.auto_filter.ref = rule_ws.dimensions

    if _data_signature(wb, bounds) != before:
        raise ValueError("Export changed original data")
    buffer = _io.BytesIO()
    wb.save(buffer)
    content = _restore_original_cells(data, buffer.getvalue())
    reopened = load_workbook(_io.BytesIO(content), keep_vba=keep_vba)
    try:
        if _data_signature(reopened, bounds) != before:
            raise ValueError("Serialized export changed original data")
        if any((reopened[name].max_row, reopened[name].max_column) != extent
               for name, extent in bounds.items()):
            raise ValueError("Serialized export expanded original data region")
    finally:
        reopened.close()
    return content, {
        "added_sheets": [name for name in wb.sheetnames if name not in original_sheets],
        "data_region_unchanged": True,
        "scan_reused": True,
        "issues": len(all_rows),
        "cells": total_cells,
        "kinds": counts,
        "sheets": per_sheet,
        "sheet": per_sheet[0]["sheet"] if per_sheet else "",
        "rule_count": len(catalog),
        "source_sha256": str(source_sha256 or actual_sha256),
        "rendering_input_sha256": actual_sha256,
        "source_representation": "original_workbook" if original_workbook else "host_table_conversion",
    }


def annotate_workbook(data: bytes, *, sheet_name: str, header_row: int,
                      findings: Iterable[dict[str, Any]],
                      row_count: int = 0, column_count: int = 0,
                      notes: Iterable[str] = (), issue_list: bool = False,
                      overview: bool = False, rules: bool = False,
                      ) -> tuple[bytes, dict[str, Any]]:
    """单表标注入口（CSV 与只用一张表的老调用方），内部走多表实现。"""
    return annotate_workbook_multi(data, targets=[{
        "sheet_name": sheet_name, "header_row": header_row, "findings": findings,
        "row_count": row_count, "column_count": column_count,
    }], notes=notes, issue_list=issue_list, overview=overview, rules=rules)
