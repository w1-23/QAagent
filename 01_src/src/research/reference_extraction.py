"""Compatibility facade for the support package's bibliography inventory.

The package owns parsing, source coordinates, limits and deterministic IDs.
Importing this facade does not import the host tool registry or runtime state.
"""
from citrus_refcheck.inventory import (
    PARSER_VERSION, MAX_BYTES, MAX_TEXT, MAX_RECORDS,
    _HEADING, _END, _ENTRY, _parse_claims,
    digest, parse_reference_claims, extract_inventory,
)

__all__ = [
    'PARSER_VERSION', 'MAX_BYTES', 'MAX_TEXT', 'MAX_RECORDS',
    'digest', 'parse_reference_claims', 'extract_inventory',
]
