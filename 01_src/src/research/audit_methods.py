"""Pure deterministic table review, shared by the host and script sandbox.

No sessions, publication, filesystem, database, network or model access. Inputs
are already parsed DataFrames with the same integer row-index contract as the
legacy audit. Findings are review leads, not proof of an error or misconduct.

``audit_frame`` preserves the existing whole-scan tuple interface and gates.
``audit_selected_checks`` filters that scan's results; it does NOT select which
rules execute, and its receipt says so explicitly. For genuine small executions,
``audit_duplicates`` and ``audit_group_outliers`` call the same primitives used
by the whole scan, without running any other rule families. Numeric cross-table
reuse remains available separately in research.integrity; it is not reimplemented.
"""
from __future__ import annotations

import json
import re
from typing import Any

import numpy as np

from src.research.data_audit_rules import RULES, describe_rule


def _pd():
    import pandas as pd
    return pd


_ANNOTATION_COLUMN_RE = re.compile(
    r"^(?:note|notes|remark|remarks|comment|comments|memo|annotation|"
    r"备注|注|说明|描述|附注|注释)$", re.I)


def _is_annotation_column(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    text = re.sub(r"[（(][^）)]*[）)]\s*$", "", text).strip()
    text = re.sub(r"^\s*\d+[.、．]\s*", "", text).strip()
    text = re.sub(r"\s*[（(]?\d+[）)]?$", "", text).strip()
    return bool(_ANNOTATION_COLUMN_RE.match(text))


def _name_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return set()
        if text[:1] in "[{":
            try:
                return _name_set(json.loads(text))
            except json.JSONDecodeError:
                text = text.strip("[]{}")
        return {part.strip().strip("\"'") for part in re.split(r"[,;，；、]", text)
                if part.strip().strip("\"'")}
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item or "").strip()}
    return {str(value).strip()}


def _group_label(columns: list[str], key) -> str:
    pairs = list(zip(columns, key)) if isinstance(key, tuple) else [(columns[0] if columns else "分组", key)]
    parts = []
    for name, value in pairs:
        if isinstance(value, float) and _pd().isna(value):
            text = "(空)"
        else:
            text = str(value)
        parts.append(f"{name}={text}")
    return " / ".join(parts) or "全表"


def _collector(frame, row_offset):
    findings: list[dict[str, Any]] = []
    issue_rows: dict[int, set[str]] = {}
    column_index_by_name = {
        str(column): index for index, column in enumerate(frame.columns, start=1)
    }

    def add(severity, check, field, detail, row=None, *, column_index=None):
        if column_index is None:
            column_index = column_index_by_name.get(str(field or ""))
        finding = {"severity": severity, "check": check, "field": str(field or ""),
                   "column_index": int(column_index) if column_index else "",
                   "row": int(row) + row_offset if row is not None else "",
                   "detail": str(detail), "rule_metadata": describe_rule(check)}
        findings.append(finding)
        if row is not None:
            issue_rows.setdefault(int(row), set()).add(check)

    return findings, issue_rows, column_index_by_name, add


def _duplicate_checks(frame, key_columns, add, *, checks):
    if 'duplicate_key' in checks:
        for col in (key_columns or []):
            if col not in frame.columns:
                continue
            keys = frame[col]
            dup = keys.notna() & keys.duplicated(keep=False)
            for idx in frame.index[dup]:
                add("review", "duplicate_key", col,
                    f"主键「{col}」的值 {str(keys.loc[idx])[:40]!r} 出现多次；请确认是重复录入还是合法的分次记录", idx)
    if 'duplicate_row' in checks:
        duplicate_mask = frame.duplicated(keep=False)
        for idx in frame.index[duplicate_mask][:10000]:
            add("review", "duplicate_row", "", "存在完全重复行，请确认是否为重复录入或合法重复测量", idx)


def _group_outlier_column(frame, col, numeric, groups, values, add):
    series = frame[col]
    valid = numeric.dropna()
    valid_groups = [c for c in groups if c in frame.columns]
    measurement_fields = {str(value) for value in values if str(value)}
    if (str(col) in measurement_fields and valid_groups
            and len(valid) >= 8
            and len(valid) / max(1, len(series.dropna())) >= 0.8):
        if valid_groups:
            grouped = frame.groupby(valid_groups[0] if len(valid_groups) == 1 else valid_groups,
                                    dropna=False, sort=False)
            partitions = [(_group_label(valid_groups, key), sub.index) for key, sub in grouped]
        skipped: list[str] = []
        for label, partition in partitions:
            local = numeric.loc[list(partition)].dropna()
            if len(local) < 8:
                if len(local):
                    skipped.append(f"{label} n={len(local)}")
                continue
            q1, q3 = float(local.quantile(.25)), float(local.quantile(.75))
            iqr = q3 - q1
            if iqr <= 0:
                continue
            low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            for idx in local[(local < low) | (local > high)].index[:10000]:
                add("review", "iqr_outlier", col,
                    f"值 {numeric.loc[idx]:g} 超出探索性 IQR 范围 [{low:g}, {high:g}]"
                    f"（口径：{label}，n={len(local)}，1.5×IQR）；不代表错误，建议结合实验背景复核", idx)
        if skipped:
            add("info", "iqr_insufficient", col,
                "分组样本不足 8，未做组内离群判定（无法可靠判定，不做推断）：" + "、".join(skipped[:12]))


def _selected(checks, allowed):
    if not isinstance(checks, (list, tuple, set, frozenset)) or any(
            not isinstance(check, str) or check not in allowed for check in checks):
        raise ValueError('Checks must be a collection of supported check names')
    return set(checks)


def audit_duplicates(frame, *, key_columns=None, row_offset=2,
                     checks=('duplicate_key', 'duplicate_row')):
    """Execute only requested duplicate rules using the whole-scan primitives.

    A key-only call never runs full-row duplication. Missing key columns retain
    the legacy skip behavior; this method does not imply a schema validation.
    """
    chosen = _selected(checks, {'duplicate_key', 'duplicate_row'})
    findings, issue_rows, _, add = _collector(frame, row_offset)
    if frame.empty:
        return findings, issue_rows
    _duplicate_checks(frame, key_columns, add, checks=chosen)
    return findings, issue_rows


def audit_group_outliers(frame, *, groups, values, row_offset=2):
    """Execute only group IQR checks; no ungrouped fallback or other scans.

    Keeps the original >=8 total/partition and >=80% numeric gates, 1.5*IQR,
    zero-IQR skip, 10000 outliers/partition bound and insufficient-group notice.
    No finding can mean prerequisites were absent, not that a check passed.
    """
    findings, issue_rows, _, add = _collector(frame, row_offset)
    measurement_fields = {str(value) for value in values if str(value)}
    for col in frame.columns:
        if str(col) not in measurement_fields or int(frame[col].notna().sum()) == 0:
            continue
        numeric = _pd().to_numeric(frame[col], errors='coerce')
        _group_outlier_column(frame, col, numeric, groups, values, add)
    return findings, issue_rows


def audit_frame(frame, mapping: dict[str, str], groups: list[str], values: list[str],
                row_offset: int = 2, integer_data_confirmed: bool = False,
                sd_kind: str = "", optional_fields: list[str] | None = None,
                ranges: dict[str, dict] | None = None,
                relations: list[dict] | None = None,
                key_columns: list[str] | None = None,
                required_fields: list[str] | None = None,
                mean_decimals: int | None = None, sd_decimals: int | None = None,
                execution_receipt: dict | None = None):
    findings, issue_rows, column_index_by_name, add = _collector(frame, row_offset)
    if frame.empty:
        add("error", "empty_table", "", "没有数据行可供检查")
        return findings, issue_rows
    optional = _name_set(optional_fields)
    required = _name_set(required_fields)
    # Required declarations win over optional/default empty-column handling.
    empty_columns = {col for col in frame.columns if int(frame[col].notna().sum()) == 0}
    for col in empty_columns:
        if str(col) not in required:
            add("info", "empty_column", col, "整列为空（可能是公式未缓存或预留空白列），未执行该列数值检查")
    for col in required - {str(column) for column in frame.columns}:
        add("error", "required_field_absent", col, "显式声明的必填字段不在表格中，未能检查其内容")
    optional -= required
    covered = _name_set(list((ranges or {}).keys())) | _name_set(key_columns or [])
    covered |= _name_set(values or []) | _name_set(groups or [])
    covered |= _name_set(list((mapping or {}).keys())) | _name_set(list((mapping or {}).values()))
    for rel in (relations or []):
        if isinstance(rel, dict):
            covered |= _name_set([rel.get("left"), rel.get("right")])
    annotation_columns = {
        str(col) for col in frame.columns
        if col not in empty_columns
        and str(col) not in optional
        and str(col) not in required
        and str(col) not in covered
        and _is_annotation_column(str(col))
    }
    optional_columns = optional | annotation_columns
    for col in sorted(annotation_columns):
        add("info", "optional_column", col,
            f"「{col}」是备注/说明类自由文本列，未对其配置任何规则，已按可选字段处理，"
            f"不逐格标注空值；若该列应为必填，请在规则中显式声明")
    for col in frame.columns:
        if col in empty_columns:
            continue
        series = frame[col]
        missing = int(series.isna().sum())
        if missing and missing / max(1, len(series)) >= 0.5:
            handling = ("已按可选字段/备注列处理，不逐格标注"
                        if str(col) in optional_columns else "已逐格标注")
            add("info", "missing_values", col,
                f"{missing}/{len(series)} 行为空（{missing / len(series):.1%}），{handling}")
        if series.nunique(dropna=True) <= 1 and len(series.dropna()) > 0:
            add("warning", "constant_field", col, "非空值只有一个唯一取值；请确认该字段是否有信息量")
        numeric = _pd().to_numeric(series, errors="coerce")
        valid = numeric.dropna()
        nonempty_count = int(series.notna().sum())
        failed_numeric = nonempty_count - len(valid)
        if len(valid) and failed_numeric:
            add("review", "mixed_numeric_text", col,
                f"{failed_numeric} 个非空值无法解析为数值；可能混有单位、符号或文本类别")
        _group_outlier_column(frame, col, numeric, groups, values, add)
    # Cell-level missingness / numeric parsing / ranges / relations / keys.
    for col in frame.columns:
        if str(col) in optional_columns or (col in empty_columns and str(col) not in required):
            continue
        series = frame[col]
        for idx in series.index[series.isna()]:
            if str(col) in required:
                add("error", "missing_cell", col, f"显式声明的必填字段「{col}」此处为空", idx)
            else:
                add("review", "missing_cell", col, f"「{col}」此处为空；若该字段允许缺失可忽略", idx)
    bad_cells: dict[int, list[str]] = {}
    for col in frame.columns:
        if col in empty_columns:
            continue
        series = frame[col]
        numeric = _pd().to_numeric(series, errors="coerce")
        nonempty = int(series.notna().sum())
        if nonempty < 5 or int(numeric.notna().sum()) / max(1, nonempty) < 0.8:
            continue
        for idx in series.index[series.notna() & numeric.isna()]:
            bad_cells.setdefault(int(idx), []).append(str(col))
    header_like: set[int] = set()
    if len(bad_cells) and frame.index.min() is not None:
        first_row = int(frame.index.min())
        for idx, cols in bad_cells.items():
            if idx == first_row and len(cols) >= 2:
                header_like.add(idx)
        if header_like:
            cols = bad_cells[int(frame.index.min())]
            add("info", "multi_header_suspected", "",
                f"第 {first_row + row_offset} 行有 {len(cols)} 列是文本（{('、'.join(cols[:6]))}），"
                f"疑似二级表头或单位行，已跳过该行的逐格标注；如确实存在多行表头，"
                f"请用 header_rows_json 指定后重新审查")
    for idx in sorted(bad_cells):
        if idx in header_like:
            continue
        row_values = frame.loc[idx]
        for col in bad_cells[idx]:
            add("review", "non_numeric_cell", col,
                f"「{col}」本应是数值，但值是 {str(row_values[col])[:40]!r}", idx)
    for col, bound in (ranges or {}).items():
        if col not in frame.columns or not isinstance(bound, dict):
            continue
        numeric = _pd().to_numeric(frame[col], errors="coerce")
        low = bound.get("min"); high = bound.get("max")
        for idx, value in numeric.items():
            if _pd().isna(value):
                continue
            number = float(value)
            if (low is not None and number < float(low)) or (high is not None and number > float(high)):
                add("review", "out_of_range", col,
                    f"值 {number:g} 超出预设合理范围 [{low if low is not None else '-inf'}, "
                    f"{high if high is not None else 'inf'}]（规则来自用户设定）", idx)
    for rel in (relations or []):
        if not isinstance(rel, dict):
            continue
        left, op, right = str(rel.get("left") or ""), str(rel.get("op") or "<="), str(rel.get("right") or "")
        if left not in frame.columns or right not in frame.columns:
            continue
        a = _pd().to_numeric(frame[left], errors="coerce")
        b = _pd().to_numeric(frame[right], errors="coerce")
        for idx in frame.index[a.notna() & b.notna()]:
            x, y = float(a.loc[idx]), float(b.loc[idx])
            ok = {"<=": x <= y, "<": x < y, ">=": x >= y, ">": x > y, "==": x == y}.get(op, True)
            if not ok:
                add("review", "logic_conflict", left,
                    f"「{left}」={x:g} 与「{right}」={y:g} 不满足 {left} {op} {right}", idx)
    _duplicate_checks(frame, key_columns, add, checks={'duplicate_key', 'duplicate_row'})
    # These numeric-forensics cues are review signals, not judgments of intent.
    numeric_cols = []
    unscanned_numeric_columns = []
    for col in frame.columns:
        numeric = _pd().to_numeric(frame[col], errors="coerce")
        if int(numeric.notna().sum()) >= 5 and numeric.notna().mean() >= .8:
            # Bound quadratic pair comparisons by eligible numeric columns;
            # identifiers and text labels do not consume this budget.
            if len(numeric_cols) < 30:
                numeric_cols.append((col, numeric))
            else:
                unscanned_numeric_columns.append(str(col))
    numeric_coverage = {
        "checked_columns": [str(col) for col, _ in numeric_cols],
        "unscanned_columns": unscanned_numeric_columns,
        "row_limit": 20_000,
        "rows_truncated": False,
    }
    if execution_receipt is not None:
        execution_receipt["numeric_forensics"] = numeric_coverage
    for i, (left_name, left) in enumerate(numeric_cols):
        for right_name, right in numeric_cols[i + 1:]:
            valid = left.notna() & right.notna()
            if int(valid.sum()) < 5:
                continue
            positions = frame.index[valid]
            if len(positions) > 20_000:
                numeric_coverage["rows_truncated"] = True
                positions = positions[:20_000]
            a = left.loc[positions].astype(float); b = right.loc[positions].astype(float)
            pair_count = len(a)
            detail = ""
            if _pd().Series(a.to_numpy()).equals(_pd().Series(b.to_numpy())):
                detail = f"{pair_count} 个共同非空位置逐值完全相同"
                check = "identical_columns"
            else:
                delta = b - a
                scale = max(float(a.abs().max()), float(b.abs().max()), 1.0)
                tolerance = max(1e-9, scale * 1e-9)
                if float(delta.max() - delta.min()) <= tolerance:
                    detail = f"两列在 {pair_count} 个共同位置的差值近似固定（约 {float(delta.median()):g}）"
                    check = "constant_offset_columns"
                elif float(a.abs().max()) > tolerance:
                    ratio_mask = a.abs() > tolerance
                    ratios = b[ratio_mask] / a[ratio_mask]
                    if len(ratios) >= 5 and float(ratios.max() - ratios.min()) <= max(1e-8, float(ratios.abs().max()) * 1e-8):
                        detail = f"两列在 {len(ratios)} 个共同位置的比值近似固定（约 {float(ratios.median()):g}）"
                        check = "constant_ratio_columns"
                if not detail:
                    try:
                        slope, intercept = np.polyfit(a.to_numpy(), b.to_numpy(), 1)
                        residual = b.to_numpy() - (slope * a.to_numpy() + intercept)
                        if float(np.max(np.abs(residual))) <= tolerance:
                            detail = f"两列近似满足线性关系 y={slope:g}x+{intercept:g}（{pair_count} 点）"
                            check = "exact_linear_columns"
                    except Exception:
                        pass
            if detail:
                add("review", check, f"{left_name} ↔ {right_name}",
                    detail + "；可能来自换算、归一化、公式或共享对照，需结合方法学确认",
                    column_index=column_index_by_name.get(str(left_name)))
    for col, numeric in numeric_cols:
        vals = numeric.dropna().astype(float)
        precise = vals[(vals - vals.round(2)).abs() > 1e-6]
        if len(precise) < 12 or precise.nunique() < 8:
            continue
        endings = precise.map(lambda x: f"{abs(x):.6f}".split(".")[1][-2:])
        counts = endings.value_counts(normalize=True)
        common = counts[counts >= .35]
        if not common.empty:
            add("review", "decimal_ending_concentration", col,
                "小数末两位存在集中（" + ", ".join(f".{k}: {v:.0%}" for k, v in common.head(4).items()) +
                "）；均值、比例、舍入、固定分母及仪器精度也会产生此现象")
    if groups:
        absent = [c for c in groups if c not in frame.columns]
        if absent:
            add("warning", "invalid_group_mapping", ",".join(absent), "模型提供的分组字段不在表格中；未执行分组平衡检查")
        else:
            counts = frame.groupby(groups, dropna=False).size()
            if len(counts) > 1 and counts.nunique() > 1:
                distribution = "; ".join(f"{k}: n={v}" for k, v in list(counts.items())[:80])
                add("review", "unequal_group_size", ",".join(groups),
                    "分组样本数不一致（这可能是有意设计）；" + distribution,
                    column_index=column_index_by_name.get(str(groups[0])))
    valid_values = [c for c in values if c in frame.columns]
    if values and len(valid_values) != len(values):
        add("warning", "invalid_value_mapping", ",".join(set(values) - set(valid_values)),
            "指定的测量字段未全部匹配列名")
    role_cols = {str(role): str(column) for role, column in mapping.items()
                 if column in frame.columns}
    if "replicate" in role_cols and groups:
        rep = role_cols["replicate"]
        keys = [c for c in groups if c in frame.columns]
        rep_counts = frame.groupby(keys, dropna=False)[rep].nunique(dropna=True)
        if len(rep_counts) > 1 and rep_counts.nunique() > 1:
            add("review", "unequal_replicates", rep,
                "不同实验组的重复编号数不一致；尚不能判断为设计问题，请核对生物学/技术重复定义")
    if integer_data_confirmed and {"mean", "n"}.issubset(role_cols):
        if "sd" in role_cols and sd_kind not in {"sample", "population"}:
            add("info", "grim_sd_skipped", role_cols["sd"],
                "SD一致性检查未执行：请先确认报告的是样本SD还是总体SD；均值与样本量检查仍可执行")
        from src.research.summary_consistency import check_integer_summary
        for idx in frame.index:
            checks = check_integer_summary(
                frame[role_cols["mean"]].loc[idx], frame[role_cols["n"]].loc[idx],
                frame[role_cols["sd"]].loc[idx] if "sd" in role_cols else None,
                mean_decimals=mean_decimals, sd_decimals=sd_decimals, sd_kind=sd_kind)
            for result in checks:
                if execution_receipt is not None:
                    receipt = execution_receipt.setdefault(result["check"], {"checked_rows": 0, "skipped_rows": 0})
                    receipt["checked_rows" if result["status"] == "checked" else "skipped_rows"] += 1
                field = role_cols.get("sd", role_cols["mean"]) if result["check"] == "integer_summary_inconsistent" else role_cols["mean"]
                if result["status"] == "not_checked":
                    add("info", "integer_summary_not_checked", field,
                        "整数汇总一致性未检查：" + result["reason"], idx)
                    findings[-1]["evidence"] = result
                elif result.get("inconsistent"):
                    interval = result.get("sum_interval", result.get("sum_of_squares_interval"))
                    add("review", result["check"], field,
                        f"按报告精度推导的区间 {interval} 不包含整数；" + result["caveat"], idx)
                    findings[-1]["evidence"] = result
    if not groups:
        add("info", "design_checks_skipped", "", "未确认实验分组字段，只执行通用数据检查")
    if len(findings) > 20000:
        dropped = len(findings) - 20000
        findings = findings[:20000]
        findings.append({
            "severity": "info", "check": "findings_truncated", "field": "",
            "column_index": "", "row": "",
            "detail": f"发现条数超过单次上限，另有 {dropped} 条未列出；请缩小范围或分批审查，勿据此判断其余数据无异常。",
            "rule_metadata": describe_rule("findings_truncated"),
        })
    return findings, issue_rows


def audit_selected_checks(frame, *, checks, mapping=None, groups=None, values=None, **kwargs):
    """Filter a whole scan by catalogue check name, with an honest scope receipt.

    This does not claim per-rule execution where the legacy engine has no such
    instrumentation: gates, caps, skipped findings and integer row receipts are
    exposed separately. A requested check may have no result because it did not
    apply, not because it passed. Use the minimal methods for selective execution.
    """
    chosen = _selected(checks, RULES)
    receipt = kwargs.pop('execution_receipt', None)
    if receipt is None:
        receipt = {}
    findings, issue_rows = audit_frame(frame, mapping or {}, groups or [], values or [],
                                      execution_receipt=receipt, **kwargs)
    selected = [finding for finding in findings if finding['check'] in chosen]
    selected_rows = {row: kinds & chosen for row, kinds in issue_rows.items() if kinds & chosen}
    notices = [finding for finding in findings if finding['check'] in {
        'empty_table', 'design_checks_skipped', 'invalid_group_mapping', 'invalid_value_mapping',
        'iqr_insufficient', 'grim_sd_skipped', 'integer_summary_not_checked', 'findings_truncated'}]
    return {'findings': selected, 'issue_rows': selected_rows, 'execution': {
        'mode': 'full_scan_result_filter', 'selection_affects_execution': False,
        'returned_checks': sorted(chosen), 'pipeline': 'audit_frame',
        'pipeline_scope': 'empty_table_only' if frame.empty else 'whole_table_scan',
        'rule_applicability': 'original_per_rule_conditions',
        'individual_rule_execution': 'not_fully_instrumented',
        'integer_summary_receipt': receipt, 'coverage_notices': notices,
        'full_findings_count': len(findings),
        'findings_truncated': any(item['check'] == 'findings_truncated' for item in findings),
        'notice': 'Selection filters results only; the original full pipeline is invoked with '
                  'its original early exits and applicability gates. '
                  'No finding does not establish that a rule ran or passed.',
    }}
