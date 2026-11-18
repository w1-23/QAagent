"""Legacy numeric reuse API, implemented by independent citrus_audit.

Finite decimal values remain exact Fractions. Original coordinates, missing
sequence breaks, bounded searches and review-only conclusions belong to the
package implementation; importing this adapter loads no host state.
"""
from citrus_audit.integrity import (
    METHOD_VERSION, METHODS, CAUTION, NORMAL_EXPLANATIONS,
    MAX_NUMERIC_CHARACTERS, MAX_SIGNIFICANT_DIGITS, MAX_ABSOLUTE_EXPONENT,
    DEFAULT_MAX_INPUT_ROWS, DEFAULT_MAX_INPUT_COLUMNS, DEFAULT_MAX_INPUT_CELLS,
    IntegrityInputLimitError, IntegrityTable, IntegrityConfig,
    table_from_rows, table_from_frame, audit_numeric_reuse,
    _hash, _number, _rational, _cell, _location, _mapped_rows, _transform,
    _decimal_tail,
)

__all__ = [
    'METHOD_VERSION', 'METHODS', 'CAUTION', 'NORMAL_EXPLANATIONS',
    'MAX_NUMERIC_CHARACTERS', 'MAX_SIGNIFICANT_DIGITS', 'MAX_ABSOLUTE_EXPONENT',
    'DEFAULT_MAX_INPUT_ROWS', 'DEFAULT_MAX_INPUT_COLUMNS', 'DEFAULT_MAX_INPUT_CELLS',
    'IntegrityInputLimitError', 'IntegrityTable', 'IntegrityConfig',
    'table_from_rows', 'table_from_frame', 'audit_numeric_reuse',
]
