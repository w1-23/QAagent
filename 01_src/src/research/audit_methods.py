"""Compatibility imports for the independent :mod:`citrus_audit` engine.

``audit_frame`` retains the legacy tuple and row-offset API. ``checks`` and
``audit_selected_checks`` now select actual branches, with requested/actual/gap
receipts. They never run a whole scan merely to filter its findings.

The old ``decimal_ending_concentration`` rule is available only when explicitly
requested. It examines absolute floats formatted to six decimal places, not
original precision, and is descriptive rather than a significance test. Use
``raw_numeric_tail`` with a validated TableEnvelope for actual lexical endings;
a legacy ``lexical_precision_preserved`` boolean alone is not evidence.
"""
from citrus_audit.methods import (
    METHOD_VERSION,
    audit_frame,
    audit_selected_checks,
    audit_duplicates,
    audit_group_outliers,
    _record,
    _exact_series,
    _finish_receipts,
    _pd,
    _is_annotation_column,
    _name_set,
    _group_label,
    _collector,
    _duplicate_checks,
    _group_outlier_column,
    _selected,
    _optional_checks,
    _structure_checks,
)
from citrus_audit.integrity import _number, _rational, _transform, IntegrityConfig
from citrus_audit.data_audit_rules import RULES, describe_rule
from citrus_audit.envelope import TableEnvelope
from citrus_audit.api import AuditSnapshot, audit_table, review_bytes, export_workbook
from citrus_audit.readers import read_table, read_tables

__all__ = [
    'METHOD_VERSION', 'audit_frame', 'audit_selected_checks', 'audit_duplicates',
    'audit_group_outliers', 'TableEnvelope', 'AuditSnapshot', 'audit_table',
    'review_bytes', 'export_workbook', 'read_table', 'read_tables',
]
