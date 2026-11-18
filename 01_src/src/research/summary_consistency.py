"""Legacy summary APIs backed by citrus_audit, without host state imports.

Integer summaries check necessary conditions under declared rounding intervals;
passing does not prove realizability or authenticity. Raw/report comparisons
require explicit cells, precision, sample unit and matching units.
"""
from citrus_audit.summary_consistency import (
    VERSION, check_integer_summary, compare_reported_summaries,
    _interval, _contains_integer, _square_bounds,
)

__all__ = ['VERSION', 'check_integer_summary', 'compare_reported_summaries']
