"""Legacy figure imports backed by the independent citrus_figures kernel.

Explicit imports retain the historical helpers without aliasing host modules.
"""
from citrus_figures.dataset_codec import (
    FORMAT,
    VERSION,
    MAX_CELLS,
    MAX_COLUMNS,
    _encode,
    _decode,
    _dtype,
    _restore_dtype,
    encode_frame,
    decode_frame,
)
