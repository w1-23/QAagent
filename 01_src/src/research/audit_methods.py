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
from collections import Counter
from fractions import Fraction

from src.research.integrity import _number, _rational, _transform, IntegrityConfig

METHOD_VERSION = "table-audit/2"


def _record(receipts, check, *, tested=0, skipped=0, reason="", status="", scope=None, **bounds):
    """Update only from an executed branch; counts describe units in scope."""
    if receipts is None:
        return
    entry = receipts.setdefault(check, {
        "check": check, "method_version": METHOD_VERSION, "status": "skipped",
        "tested": 0, "skipped": 0, "reasons": {}, "scopes": [], "scopes_omitted": 0,
    })
    entry["tested"] += int(tested)
    entry["skipped"] += int(skipped)
    if reason:
        entry["reasons"][reason] = entry["reasons"].get(reason, 0) + max(1, int(skipped))
    if scope is not None:
        if len(entry["scopes"]) < 100:
            entry["scopes"].append(scope)
        else:
            entry["scopes_omitted"] += 1
    entry.update(bounds)
    if status in {"invalid_input", "failed", "truncated"}:
        priority = {'skipped': 0, 'executed': 0, 'truncated': 1, 'invalid_input': 2, 'failed': 3}
        if priority[status] >= priority[entry['status']]:
            entry["status"] = status
    elif entry["status"] not in {"invalid_input", "failed", "truncated"}:
        entry["status"] = "executed" if entry["tested"] else "skipped"


def _exact_series(series):
    # dtype=object prevents pandas from converting large exact integers to float.
    return _pd().Series([_number(value)[0] for value in series], index=series.index, dtype=object)


def _finish_receipts(receipt, findings, source_coverage):
    rules = receipt["rules"]
    for key in RULES:
        if key not in rules:
            _record(rules, key, reason="not_applicable_or_not_selected")
    counts = Counter(item["check"] for item in findings)
    for key, entry in rules.items():
        entry["findings_returned"] = counts[key]
        entry["source_coverage"] = {key: value for key, value in (source_coverage or {}).items()
                                    if key in {'name', 'sheet', 'source_rows', 'source_columns', 'processed_rows',
                                               'processed_columns', 'truncated', 'truncation_reasons',
                                               'source_rows_exact', 'lexical_precision_preserved', 'formula_evaluation'}}
        if (source_coverage or {}).get("truncated") and entry["tested"]:
            _record(rules, key, status="truncated", reason="source_not_fully_loaded")
    receipt["method_version"] = METHOD_VERSION

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


def _collector(frame, row_offset, *, max_findings=None, omitted=None):
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
        if max_findings is None or len(findings) < max_findings:
            findings.append(finding)
        elif omitted is not None:
            omitted[check] += 1
        if row is not None:
            issue_rows.setdefault(int(row), set()).add(check)
        return finding

    return findings, issue_rows, column_index_by_name, add


def _duplicate_checks(frame, key_columns, add, *, checks, receipts=None):
    if 'duplicate_key' in checks:
        for col in (key_columns or []):
            if col not in frame.columns:
                _record(receipts, 'duplicate_key', status='invalid_input', skipped=1, reason='column_absent', scope={'column': col})
                continue
            keys = frame[col]
            _record(receipts, 'duplicate_key', tested=len(keys), scope={'column': col, 'unit': 'rows'})
            dup = keys.notna() & keys.duplicated(keep=False)
            for idx in frame.index[dup]:
                add("error", "duplicate_key", col,
                    f"显式唯一键「{col}」的值 {str(keys.loc[idx])[:40]!r} 出现多次，违反声明的唯一性约束；请核对原始记录或修正键定义", idx)
    if 'duplicate_row' in checks:
        duplicate_mask = frame.duplicated(keep=False)
        _record(receipts, 'duplicate_row', tested=len(frame), scope={'unit': 'rows'},
                finding_limit=10000, findings_omitted=max(0, int(duplicate_mask.sum()) - 10000),
                status='truncated' if int(duplicate_mask.sum()) > 10000 else '')
        for idx in frame.index[duplicate_mask][:10000]:
            add("review", "duplicate_row", "", "存在完全重复行，请确认是否为重复录入或合法重复测量", idx)


def _group_outlier_column(frame, col, numeric, groups, values, add, receipts=None):
    series = frame[col]
    valid = numeric.dropna()
    valid_groups = [c for c in groups if c in frame.columns]
    measurement_fields = {str(value) for value in values if str(value)}
    if (str(col) in measurement_fields and valid_groups and len(valid_groups) == len(groups)
            and len(valid) >= 8
            and len(valid) / max(1, len(series.dropna())) >= 0.8):
        if valid_groups:
            grouped = frame.groupby(valid_groups[0] if len(valid_groups) == 1 else valid_groups,
                                    dropna=False, sort=False)
            partitions = [(_group_label(valid_groups, key), sub.index) for key, sub in grouped]
        skipped: list[str] = []
        for label, partition in partitions:
            local = _exact_series(series.loc[list(partition)]).dropna()
            if len(local) < 8:
                _record(receipts, 'iqr_outlier', skipped=1, reason='group_n_below_8',
                        scope={'column': str(col), 'group': label, 'n': len(local)})
                if len(local):
                    skipped.append(f"{label} n={len(local)}")
                continue
            # NumPy selects quartile neighbors; Fraction performs the standard
            # linear interpolation without rounding a large integer baseline.
            ordered = np.sort(local.to_numpy(dtype=object))
            def quartile(numerator):
                position = Fraction((len(ordered)-1)*numerator, 4)
                lower = position.numerator // position.denominator
                upper = min(lower+1, len(ordered)-1)
                return ordered[lower] + (ordered[upper]-ordered[lower]) * (position-lower)
            q1, q3 = quartile(1), quartile(3)
            iqr = q3 - q1
            if iqr <= 0:
                _record(receipts, 'iqr_outlier', skipped=1, reason='zero_iqr',
                        scope={'column': str(col), 'group': label, 'n': len(local)})
                continue
            _record(receipts, 'iqr_outlier', tested=len(local), scope={
                'column': str(col), 'group': label, 'n': len(local), 'unit': 'observations'},
                minimum_n=8, multiplier=1.5, finding_limit_per_group=10000)
            low, high = q1 - Fraction(3, 2) * iqr, q3 + Fraction(3, 2) * iqr
            outliers = local[(local < low) | (local > high)].index
            if len(outliers) > 10000:
                _record(receipts, 'iqr_outlier', status='truncated', reason='finding_limit_per_group',
                        findings_omitted=len(outliers)-10000)
            for idx in outliers[:10000]:
                add("review", "iqr_outlier", col,
                    f"值 {series.loc[idx]} 超出探索性 IQR 范围 [{_rational(low)}, {_rational(high)}]"
                    f"（口径：{label}，n={len(local)}，1.5×IQR）；不代表错误，建议结合实验背景复核", idx)
        if skipped:
            add("info", "iqr_insufficient", col,
                "分组样本不足 8，未做组内离群判定（无法可靠判定，不做推断）：" + "、".join(skipped[:12]))
    elif str(col) in measurement_fields:
        _record(receipts, 'iqr_outlier', skipped=1, reason='requires_group_n8_and_numeric_fraction_0.8',
                scope={'column': str(col), 'finite_rows': len(valid)})


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
                execution_receipt: dict | None = None,
                composite_keys: list[list[str]] | None = None,
                pattern_checks: dict | None = None,
                expected_structure: dict | None = None,
                source_coverage: dict | None = None):
    receipt = execution_receipt if execution_receipt is not None else {}
    receipt.clear()
    receipts = receipt['rules'] = {}
    omitted = Counter()
    findings, issue_rows, column_index_by_name, collect = _collector(frame, row_offset, max_findings=20000, omitted=omitted)
    def add(*args, **kwargs):
        finding = collect(*args, **kwargs)
        check = args[1]
        if check not in receipts:
            _record(receipts, check, tested=1, scope={'unit': 'diagnostic'})
        return finding
    operators = {'<=': lambda a,b: a <= b, '<': lambda a,b: a < b,
                 '>=': lambda a,b: a >= b, '>': lambda a,b: a > b, '==': lambda a,b: a == b}
    valid_relations = []
    for rel in relations or []:
        if (not isinstance(rel, dict) or not isinstance(rel.get('op', '<='), str)
                or rel.get('op', '<=') not in operators):
            _record(receipts, 'logic_conflict', status='invalid_input', reason='invalid_operator_config', scope={'config': rel})
        else:
            valid_relations.append(rel)
    _record(receipts, 'empty_table', tested=1, scope={'rows': len(frame), 'columns': len(frame.columns)})
    if frame.empty:
        add("error", "empty_table", "", "没有数据行可供检查")
        _finish_receipts(receipt, findings, source_coverage)
        return findings, issue_rows
    optional = _name_set(optional_fields)
    required = _name_set(required_fields)
    # Required declarations win over optional/default empty-column handling.
    empty_columns = {col for col in frame.columns if int(frame[col].notna().sum()) == 0}
    _record(receipts, 'empty_column', tested=len(frame.columns), scope={'unit': 'columns', 'rows': len(frame)})
    _record(receipts, 'required_field_absent', tested=len(required), scope={'unit': 'declared_fields'})
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
        for check in ('missing_values', 'constant_field', 'mixed_numeric_text'):
            _record(receipts, check, tested=len(series), scope={'column': str(col), 'unit': 'cells'})
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
        _group_outlier_column(frame, col, numeric, groups, values, add, receipts)
    # Cell-level missingness / numeric parsing / ranges / relations / keys.
    for col in frame.columns:
        if str(col) in optional_columns or (col in empty_columns and str(col) not in required):
            continue
        series = frame[col]
        _record(receipts, 'missing_cell', tested=len(series), scope={'column': str(col), 'unit': 'cells'})
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
            _record(receipts, 'non_numeric_cell', skipped=1, reason='numeric_gate', scope={'column': str(col)})
            continue
        _record(receipts, 'non_numeric_cell', tested=nonempty, scope={'column': str(col), 'unit': 'cells'},
                minimum_nonempty=5, minimum_numeric_fraction=0.8)
        for idx in series.index[series.notna() & numeric.isna()]:
            bad_cells.setdefault(int(idx), []).append(str(col))
    _record(receipts, 'multi_header_suspected', tested=1, scope={'unit': 'first_data_row', 'row': int(frame.index.min()) + row_offset})
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
            add("error" if col in (ranges or {}) else "review", "non_numeric_cell", col,
                f"「{col}」本应是数值，但值是 {str(row_values[col])[:40]!r}", idx)
    for col, bound in (ranges or {}).items():
        if not isinstance(bound, dict) or not set(bound).intersection({'min', 'max'}):
            _record(receipts, 'out_of_range', status='invalid_input', reason='invalid_range', scope={'column': col})
            continue
        low, low_reason = _number(bound['min']) if bound.get('min') is not None else (None, '')
        high, high_reason = _number(bound['max']) if bound.get('max') is not None else (None, '')
        if low_reason or high_reason or (low is None and high is None) or (low is not None and high is not None and low > high):
            _record(receipts, 'out_of_range', status='invalid_input', reason='invalid_range_bounds', scope={'column': col})
            continue
        if col not in frame.columns:
            _record(receipts, 'out_of_range', status='invalid_input', skipped=1, reason='column_absent', scope={'column': col})
            continue
        numeric = _exact_series(frame[col])
        _record(receipts, 'out_of_range', tested=int(numeric.notna().sum()),
                skipped=int(numeric.isna().sum()), scope={'column': col, 'unit': 'cells', 'bounds': bound})
        for idx, number in numeric.items():
            if number is None:
                if _pd().notna(frame[col].loc[idx]) and (idx in header_like or col not in bad_cells.get(int(idx), [])):
                    add('error', 'non_numeric_cell', col,
                        f'显式数值范围要求可解析的数值，实际为 {str(frame[col].loc[idx])[:40]!r}', idx)
                continue
            if (low is not None and number < low) or (high is not None and number > high):
                add('error', 'out_of_range', col,
                    f"值 {_rational(number)} 超出预设合理范围 [{bound.get('min', '-inf')}, "
                    f"{bound.get('max', 'inf')}]（规则来自用户设定）", idx)
    for rel in valid_relations:
        left, op, right = str(rel.get('left') or ''), rel.get('op', '<='), str(rel.get('right') or '')
        if left not in frame.columns or right not in frame.columns:
            _record(receipts, 'logic_conflict', status='invalid_input', skipped=1, reason='column_absent', scope={'config': rel})
            continue
        a, b = _exact_series(frame[left]), _exact_series(frame[right])
        positions = frame.index[a.notna() & b.notna()]
        _record(receipts, 'logic_conflict', tested=len(positions), skipped=len(frame)-len(positions),
                scope={'config': rel, 'unit': 'paired_rows'})
        for idx in positions:
            x, y = a.loc[idx], b.loc[idx]
            if not operators[op](x, y):
                add('review', 'logic_conflict', left,
                    f'「{left}」={_rational(x)} 与「{right}」={_rational(y)} 不满足 {left} {op} {right}', idx)
    _duplicate_checks(frame, key_columns, add, checks={'duplicate_key', 'duplicate_row'}, receipts=receipts)
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
    # Use the existing exact transform implementation; scale tolerance by
    # variation so a large baseline cannot hide discrepant observations.
    exact_columns = {name: _exact_series(frame[name]) for name, _ in numeric_cols}
    pair_rules = ('identical_columns', 'constant_offset_columns', 'constant_ratio_columns', 'exact_linear_columns')
    transform_names = ('exact_column', 'constant_offset', 'constant_ratio', 'affine')
    config = IntegrityConfig()
    for i, (left_name, _) in enumerate(numeric_cols):
        for right_name, _ in numeric_cols[i + 1:]:
            left, right = exact_columns[left_name], exact_columns[right_name]
            positions = frame.index[left.notna() & right.notna()]
            if len(positions) < 5:
                for check in pair_rules:
                    _record(receipts, check, skipped=1, reason='fewer_than_5_common_rows')
                continue
            total = len(positions)
            positions = positions[:20000]
            numeric_coverage['rows_truncated'] |= total > len(positions)
            a, b = list(left.loc[positions]), list(right.loc[positions])
            matched = False
            for check, method in zip(pair_rules, transform_names):
                if matched or (method == 'constant_ratio' and not any(a)) or (method == 'affine' and len(set(a)) < 2):
                    _record(receipts, check, skipped=1, reason='stronger_relation_or_degenerate_input')
                    continue
                _record(receipts, check, tested=1, scope={
                    'columns': [str(left_name), str(right_name)], 'unit': 'column_pairs',
                    'common_rows': total, 'used_rows': len(positions),
                    'row_start': int(positions[0])+row_offset, 'row_end': int(positions[-1])+row_offset},
                    row_limit=20000, column_limit=30, absolute_tolerance=config.absolute_tolerance,
                    relative_tolerance=config.relative_tolerance, tolerance_scale='variation')
                try:
                    relation = ({'exact': True} if a == b else None) if method == 'exact_column' else _transform(a, b, method, config)
                except (ArithmeticError, ValueError, TypeError) as exc:
                    _record(receipts, check, status='failed', reason=type(exc).__name__,
                            scope={'columns': [str(left_name), str(right_name)], 'error': str(exc)[:200]})
                    continue
                if relation is not None:
                    signal = add('review', check, f'{left_name} ↔ {right_name}',
                        f'{len(a)} 个共同位置的数值关系 {relation}；可能来自换算、归一化、公式或共享对照，需结合方法学确认',
                        column_index=column_index_by_name.get(str(left_name)))
                    signal['evidence'] = {'relationship': relation, 'rows': [int(x)+row_offset for x in positions]}
                    matched = True
    if numeric_coverage['rows_truncated'] or unscanned_numeric_columns:
        for check in pair_rules:
            _record(receipts, check, status='truncated', reason='numeric_pair_search_limit',
                    unscanned_columns=unscanned_numeric_columns)
    for col, numeric in numeric_cols:
        vals = numeric.dropna().astype(float)
        precise = vals[(vals - vals.round(2)).abs() > 1e-6]
        if len(precise) < 12 or precise.nunique() < 8:
            _record(receipts, 'decimal_ending_concentration', skipped=1, reason='requires_12_values_8_distinct', scope={'column': str(col)})
            continue
        _record(receipts, 'decimal_ending_concentration', tested=len(precise), scope={'column': str(col), 'unit': 'formatted_values'},
                formatting='absolute_float_six_decimal_places', frequency_threshold=0.35,
                original_lexical_precision=False)
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
            _record(receipts, 'invalid_group_mapping', status='invalid_input', reason='column_absent', scope={'columns': absent})
            add("warning", "invalid_group_mapping", ",".join(absent), "指定的分组字段不在表格中；未执行分组平衡或组内IQR检查，请修正 groups")
        else:
            counts = frame.groupby(groups, dropna=False).size()
            _record(receipts, 'unequal_group_size', tested=len(frame), scope={'groups': groups, 'partitions': len(counts), 'unit': 'rows'})
            if len(counts) > 1 and counts.nunique() > 1:
                distribution = "; ".join(f"{k}: n={v}" for k, v in list(counts.items())[:80])
                add("review", "unequal_group_size", ",".join(groups),
                    "分组样本数不一致（这可能是有意设计）；" + distribution,
                    column_index=column_index_by_name.get(str(groups[0])))
    valid_values = [c for c in values if c in frame.columns]
    if values and len(valid_values) != len(values):
        _record(receipts, 'invalid_value_mapping', status='invalid_input', reason='column_absent', scope={'columns': sorted(set(values) - set(valid_values))})
        add("warning", "invalid_value_mapping", ",".join(set(values) - set(valid_values)),
            "指定的测量字段未全部匹配列名")
    role_cols = {str(role): str(column) for role, column in mapping.items()
                 if column in frame.columns}
    unmapped_roles = {role: column for role, column in mapping.items() if column not in frame.columns}
    if unmapped_roles:
        _record(receipts, 'invalid_value_mapping', status='invalid_input', reason='role_column_absent', scope={'mapping': unmapped_roles})
        add('warning', 'invalid_value_mapping', ','.join(str(c) for c in unmapped_roles.values()),
            'column_mapping 指向不存在的列，相关角色规则未执行：' + str(unmapped_roles))
    if "replicate" in role_cols and groups and all(c in frame.columns for c in groups):
        rep = role_cols["replicate"]
        keys = [c for c in groups if c in frame.columns]
        rep_counts = frame.groupby(keys, dropna=False)[rep].nunique(dropna=True)
        _record(receipts, 'unequal_replicates', tested=int(frame[rep].notna().sum()),
                scope={'groups': keys, 'replicate': rep, 'unit': 'nonempty_ids', 'partitions': len(rep_counts)})
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
                counts = receipt.setdefault(result['check'], {'checked_rows': 0, 'skipped_rows': 0})
                counts['checked_rows' if result['status'] == 'checked' else 'skipped_rows'] += 1
                _record(receipts, result['check'], tested=int(result['status'] == 'checked'),
                        skipped=int(result['status'] != 'checked'), reason=result.get('reason', ''),
                        status=result.get('execution_status', ''),
                        scope={'row': int(idx)+row_offset, 'unit': 'summary_rows'},
                        mean_decimals=mean_decimals, sd_decimals=sd_decimals, sd_kind=sd_kind)
                field = role_cols.get("sd", role_cols["mean"]) if result["check"] == "integer_summary_inconsistent" else role_cols["mean"]
                if result["status"] == "not_checked":
                    signal = add("info", "integer_summary_not_checked", field,
                        "整数汇总一致性未检查：" + result["reason"], idx)
                    signal["evidence"] = result
                elif result.get("inconsistent"):
                    interval = result.get("sum_interval", result.get("sum_of_squares_interval"))
                    signal = add("review", result["check"], field,
                        f"按报告精度推导的区间 {interval} 不包含整数；" + result["caveat"], idx)
                    signal["evidence"] = result
    if not groups:
        add("info", "design_checks_skipped", "", "未确认实验分组字段，只执行通用数据检查")
    _optional_checks(frame, values, groups, mapping, composite_keys, pattern_checks,
                     expected_structure, row_offset, add, findings, receipts)
    if omitted:
        dropped = sum(omitted.values())
        for key, count in omitted.items():
            _record(receipts, key, status='truncated', reason='output_finding_limit', findings_omitted=count)
        findings.append({
            "severity": "info", "check": "findings_truncated", "field": "",
            "column_index": "", "row": "",
            "detail": f"发现条数超过单次上限，另有 {dropped} 条未列出；请缩小范围或分批审查，勿据此判断其余数据无异常。",
            "rule_metadata": describe_rule("findings_truncated"),
        })
    _finish_receipts(receipt, findings, source_coverage)
    return findings, issue_rows


def _optional_checks(frame, values, groups, mapping, composite_keys, patterns,
                     structure, row_offset, add, findings, receipts):
    """Opt-in descriptive patterns. Never sort values or infer experiment design."""
    for columns in composite_keys or []:
        if (not isinstance(columns, list) or len(columns) < 2
                or any(not isinstance(c, str) for c in columns) or len(set(columns)) != len(columns)):
            _record(receipts, 'duplicate_composite_key', status='invalid_input', reason='requires_distinct_key_columns')
            continue
        if any(c not in frame.columns for c in columns):
            _record(receipts, 'duplicate_composite_key', status='invalid_input', skipped=1, reason='column_absent', scope={'columns': columns})
            continue
        complete = frame[columns].notna().all(axis=1)
        duplicate = complete & frame.duplicated(subset=columns, keep=False)
        _record(receipts, 'duplicate_composite_key', tested=int(complete.sum()), skipped=int((~complete).sum()),
                scope={'columns': columns, 'unit': 'complete_keys'}, finding_limit=10000,
                status='truncated' if duplicate.sum() > 10000 else '', findings_omitted=max(0, int(duplicate.sum())-10000))
        for idx in frame.index[duplicate][:10000]:
            add('error', 'duplicate_composite_key', ','.join(columns),
                '显式复合唯一键重复，违反声明的唯一性约束；请核对原始记录或修正键定义。', idx)
    _structure_checks(frame, structure, row_offset, add, findings, receipts)
    names = ('raw_numeric_tail', 'adjacent_equal_differences', 'sliding_equal_differences',
             'repeated_sequences', 'repeated_blocks')
    if not patterns:
        for name in names:
            _record(receipts, name, reason='not_selected_requires_explicit_measurements_and_order')
        return
    if not isinstance(patterns, dict):
        for name in names:
            _record(receipts, name, status='invalid_input', reason='pattern_checks_must_be_object')
        return
    chosen = [name for name in names if patterns.get(name) is True]
    columns = patterns.get('columns', values)
    excluded = set(groups) | {c for role, c in mapping.items() if role in {'id', 'sample_id', 'replicate', 'experimental_unit', 'n'}}
    if (not isinstance(columns, list) or not columns or any(not isinstance(c, str) for c in columns)
            or len(set(columns)) != len(columns) or any(c not in frame.columns or c not in values or c in excluded for c in columns)):
        for name in chosen:
            _record(receipts, name, status='invalid_input', reason='declare_present_measurements_in_values_exclude_ids_and_groups')
        return
    window = patterns.get('window', 5)
    axis = patterns.get('axis', 'rows')
    atol, ar = _number(patterns.get('absolute_tolerance', '0'))
    rtol, rr = _number(patterns.get('relative_tolerance', '0'))
    digits = patterns.get('tail_digits', 2)
    frequency, fr = _number(patterns.get('frequency_threshold', '0.35'))
    if (type(window) is not int or not 3 <= window <= 100 or not isinstance(axis, str) or axis not in {'rows', 'columns'}
            or ar or rr or atol < 0 or rtol < 0 or type(digits) is not int or not 1 <= digits <= 6
            or fr or not 0 < frequency <= 1):
        for name in chosen:
            _record(receipts, name, status='invalid_input', reason='invalid_window_axis_tolerance_or_tail_threshold')
        return
    # A bounded prefix with an explicit source order; missing cells break windows.
    selected_columns = [c for c in frame.columns if c in columns]
    if columns != selected_columns:
        for name in chosen:
            _record(receipts, name, status='invalid_input', reason='columns_must_follow_original_order')
        return
    view = frame.loc[:, columns].iloc[:20000, :30]
    clipped = len(frame) > len(view) or len(columns) > view.shape[1]
    matrix = [[_number(v)[0] for v in row] for row in view.itertuples(index=False, name=None)]
    rows = [int(i)+row_offset for i in view.index]
    physical_cols = [int(frame.columns.get_loc(c))+1 for c in view.columns]
    for name in chosen:
        _record(receipts, name, row_limit=20000, column_limit=30, window=window,
                absolute_tolerance=str(patterns.get('absolute_tolerance', '0')),
                relative_tolerance=str(patterns.get('relative_tolerance', '0')),
                axis=axis, significance_claimed=False, search_limit=100000,
                scope={'rows': len(view), 'columns': list(view.columns), 'row_order': 'original'})
    if 'raw_numeric_tail' in chosen:
        if not frame.attrs.get('lexical_precision_preserved', False):
            _record(receipts, 'raw_numeric_tail', reason='original_numeric_tokens_unavailable')
        else:
            for col in view.columns:
                tokens = []
                positions = []
                for idx, value in view[col].items():
                    # No synthesized digits or exponent expansion. A literal mantissa
                    # ending is compared only within the same precision/exponent stratum.
                    if not isinstance(value, str) or _number(value)[0] is None:
                        continue
                    match = re.fullmatch(r'[+-]?(\d*)\.(\d+)(?:[eE]([+-]?\d+))?', value.strip())
                    if match and len(match[2]) >= digits:
                        tokens.append((len(match[2]), int(match[3] or 0), match[2][-digits:], value.strip()))
                        positions.append(int(idx)+row_offset)
                strata = {}
                for token, pos in zip(tokens, positions):
                    strata.setdefault(token[:2], []).append((token[2], pos, token[3]))
                for stratum, entries in strata.items():
                    if len(entries) < 12 or len({value for _, _, value in entries}) < 8:
                        _record(receipts, 'raw_numeric_tail', skipped=1, reason='requires_12_tokens_8_distinct', scope={'column': col, 'precision_exponent': stratum})
                        continue
                    counts = Counter(tail for tail, _, _ in entries)
                    _record(receipts, 'raw_numeric_tail', tested=len(entries), scope={
                        'column': col, 'precision_exponent': stratum, 'unit': 'tokens', 'counts': dict(counts)},
                        tail_digits=digits, frequency_threshold=str(frequency), minimum_n=12, minimum_distinct=8)
                    for tail, count in counts.items():
                        if Fraction(count, len(entries)) >= frequency:
                            signal = add('review', 'raw_numeric_tail', col,
                                f'原始词法尾数 {tail} 在同精度/指数层中出现 {count}/{len(entries)}；仪器舍入或固定分母亦可产生，不是显著性检验。')
                            signal['evidence'] = {'tail': tail, 'count': count, 'n': len(entries),
                                'precision_exponent': stratum, 'rows': [p for t,p,_ in entries if t == tail]}
                if not strata:
                    _record(receipts, 'raw_numeric_tail', skipped=1, reason='no_literal_decimal_tokens', scope={'column': col})
    ordered = [name for name in chosen if name != 'raw_numeric_tail']
    if clipped and 'raw_numeric_tail' in chosen and receipts['raw_numeric_tail']['tested']:
        _record(receipts, 'raw_numeric_tail', status='truncated', reason='input_prefix_limit')
    if not ordered:
        return
    if patterns.get('order_confirmed') is not True or (axis == 'columns' and patterns.get('same_units_confirmed') is not True):
        for name in ordered:
            _record(receipts, name, reason='confirm_meaningful_order_and_rowwise_matching_units')
        return
    sequences = ([([row[j] for row in matrix], [(r, physical_cols[j]) for r in rows]) for j in range(view.shape[1])]
                 if axis == 'rows' else [(row, [(rows[i], c) for c in physical_cols]) for i, row in enumerate(matrix)])
    def emit(name, coords, evidence):
        signal = add('review', name, '', '原始顺序中的结构信号；计划剂量、时间网格、舍入、共享对照或合法重复可产生相同模式。')
        signal['evidence'] = {'cells': [{'row': r, 'column_index': c} for r,c in coords], **evidence}
    for name in ordered:
        tested = 0
        omitted = 0
        for seq, coords in sequences if name != 'repeated_blocks' else []:
            width = 3 if name == 'adjacent_equal_differences' else window
            seen = {}
            for start in range(max(0, len(seq)-width+1)):
                if tested >= 100000:
                    omitted += len(seq)-width+1-start
                    break
                part = seq[start:start+width]
                loc = coords[start:start+width]
                dimension = 0 if axis == 'rows' else 1
                if None in part or any(b[dimension] != a[dimension]+1 for a,b in zip(loc, loc[1:])):
                    _record(receipts, name, skipped=1, reason='missing_or_noncontiguous_window')
                    continue
                tested += 1
                if name == 'repeated_sequences':
                    signature = tuple(part)
                    if len(set(part)) < 3:
                        continue
                    previous = seen.setdefault(signature, start)
                    if start - previous >= width:
                        emit(name, coords[previous:previous+width]+loc, {'length': width, 'exact': True})
                else:
                    differences = [b-a for a,b in zip(part, part[1:])]
                    tolerance = atol + rtol * (max(part)-min(part))
                    if len(set(part)) >= 3 and max(differences)-min(differences) <= tolerance:
                        emit(name, loc, {'difference': _rational(differences[0]), 'tolerance': _rational(tolerance), 'length': width})
        if name == 'repeated_blocks':
            seen = {}
            for c in range(max(0, view.shape[1]-1)):
                for start in range(max(0, len(matrix)-window+1)):
                    if tested >= 100000:
                        omitted += len(matrix)-window+1-start
                        break
                    block = tuple(tuple(row[c:c+2]) for row in matrix[start:start+window])
                    if (physical_cols[c+1] != physical_cols[c]+1 or any(b != a+1 for a,b in zip(rows[start:start+window], rows[start+1:start+window]))
                            or any(None in row for row in block) or any(len({row[j] for row in block}) < 3 for j in range(2))):
                        _record(receipts, name, skipped=1, reason='incomplete_noncontiguous_or_low_distinct_block')
                        continue
                    tested += 1
                    prev = seen.setdefault(block, (start, c))
                    if prev != (start, c) and (abs(start-prev[0]) >= window or abs(c-prev[1]) >= 2):
                        loc = [(rows[r], physical_cols[k]) for top,col in (prev, (start,c)) for r in range(top, top+window) for k in (col,col+1)]
                        emit(name, loc, {'shape': [window,2], 'exact': True})
        _record(receipts, name, tested=tested, scope={'unit': 'eligible_windows'},
                status='truncated' if clipped or omitted else '', windows_omitted=omitted,
                reason='search_or_input_limit' if clipped or omitted else ('no_eligible_windows' if not tested else ''))
    if clipped and 'raw_numeric_tail' in chosen and receipts['raw_numeric_tail']['tested']:
        _record(receipts, 'raw_numeric_tail', status='truncated', reason='input_prefix_limit')


def _structure_checks(frame, structure, row_offset, add, findings, receipts):
    check = 'expected_experiment_structure'
    if not structure:
        _record(receipts, check, reason='requires_explicit_expected_cells_and_experimental_unit')
        return
    try:
        if not isinstance(structure, dict):
            raise ValueError('expected_structure_must_be_object')
        factors = structure.get('factors')
        unit = structure.get('unit_column')
        replicate = structure.get('replicate_column')
        expected = structure.get('expected_cells')
        if (not isinstance(factors, list) or not factors or any(not isinstance(c,str) for c in factors)
                or len(set(factors)) != len(factors) or not isinstance(unit,str)
                or not isinstance(expected,list) or not expected or len(expected)>500):
            raise ValueError('explicit_factors_unit_and_at_most_500_expected_cells_required')
        needed = factors + [unit] + ([replicate] if replicate else [])
        if any(c not in frame.columns for c in needed):
            _record(receipts, check, status='invalid_input', skipped=1, reason='mapped_column_absent', scope={'columns': needed})
            return
        # Validate the entire declaration before evaluating any expected cell.
        identities = set()
        for cell in expected:
            if not isinstance(cell,dict) or any(c not in cell for c in factors):
                raise ValueError('expected_cell_missing_factor')
            identity = tuple(cell[c] for c in factors)
            if any(not isinstance(v,(str,int,float,bool)) or _pd().isna(v) for v in identity) or identity in identities:
                raise ValueError('expected_factor_values_must_be_unique_nonmissing_scalars')
            identities.add(identity)
            if type(cell.get('expected_units')) is not int or cell['expected_units'] < 1:
                raise ValueError('expected_units_must_be_positive_integer')
            if 'expected_replicates' in cell and (not replicate or type(cell['expected_replicates']) is not int or cell['expected_replicates'] < 1):
                raise ValueError('expected_replicates_requires_column_and_positive_count')
        for cell in expected:
            mask = _pd().Series(True,index=frame.index)
            for factor in factors:
                mask &= frame[factor].eq(cell[factor]).fillna(False)
            local = frame.loc[mask]
            units = int(local[unit].nunique(dropna=True))
            _record(receipts, check, tested=1, scope={'unit': 'expected_factor_cells', 'expected': cell,
                    'observed_units': units, 'observed_rows': len(local), 'missing_unit_rows': int(local[unit].isna().sum())})
            mismatch = units != cell['expected_units']
            reps = {}
            if 'expected_replicates' in cell:
                reps = {str(k): int(v) for k,v in local.groupby(unit,dropna=True)[replicate].nunique(dropna=True).items()}
                mismatch |= any(v != cell['expected_replicates'] for v in reps.values())
            if mismatch:
                signal = add('review', check, ','.join(factors), '观测到的实验单位或重复数与显式预期不一致；需核对缺失、排除政策及实验单位定义。')
                signal['evidence'] = {'expected': cell, 'observed_units': units, 'replicates_by_unit': reps,
                    'rows': [int(i)+row_offset for i in local.index]}
        observed = set(frame[factors].dropna().itertuples(index=False,name=None))
        extras = observed-identities
        if structure.get('exhaustive') is True and extras:
            signal = add('review',check,','.join(factors),'存在显式完整设计表之外的因子组合；请核对设计映射。')
            signal['evidence'] = {'unexpected_combinations': [list(x) for x in sorted(extras,key=str)[:500]],
                'unexpected_combinations_count': len(extras)}
    except (ValueError, TypeError) as exc:
        _record(receipts, check, status='invalid_input', reason=str(exc))


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
        'individual_rule_execution': 'instrumented', 'rules': receipt.get('rules', {}),
        'integer_summary_receipt': receipt, 'coverage_notices': notices,
        'full_findings_count': len(findings),
        'findings_truncated': any(item['check'] == 'findings_truncated' for item in findings),
        'notice': 'Selection filters results only; the original full pipeline is invoked with '
                  'its original early exits and applicability gates. '
                  'No finding does not establish that a rule ran or passed.',
    }}
