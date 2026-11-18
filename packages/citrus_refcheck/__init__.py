"""Evidence-oriented reference lookup without a platform/agent dependency."""
__version__ = '2.0.0'
from .lookup import LookupLimits, FixtureTransport, HttpTransport, lookup_evidence
from .compare import hint_differences
from .inventory import extract_inventory
from .audit import AuditRepository, AuditStopped, BudgetExceeded, export_snapshot
from .providers import normalize_doi, norm_title

__all__ = ['LookupLimits','FixtureTransport','HttpTransport','lookup_evidence',
           'hint_differences','extract_inventory','AuditRepository','AuditStopped','BudgetExceeded',
           'export_snapshot','normalize_doi','norm_title']
