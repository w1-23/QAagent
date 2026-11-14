"""Restricted JSON dataframe codec, shared verbatim with the figure worker.

No pickle, service imports, executable dtype names, or string coercion fallback.
Float hex and decimal/integer strings preserve values independently of JSON's
number precision. Dataframe attrs are deliberately not part of the wire format.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import math
import re
from typing import Any

FORMAT = "citrus.dataset"
VERSION = 1
MAX_CELLS = 2_000_000
MAX_COLUMNS = 4096


def _encode(value: Any) -> dict:
    import numpy as np
    import pandas as pd

    if value is None:
        return {"t": "none"}
    if value is pd.NA:
        return {"t": "na"}
    if value is pd.NaT:
        return {"t": "nat"}
    if isinstance(value, pd.Timestamp):
        return {"t": "timestamp", "v": value.isoformat(),
                "tz": str(value.tz) if value.tz is not None else None}
    if isinstance(value, pd.Timedelta):
        return {"t": "timedelta", "v": str(value.value)}
    if isinstance(value, np.datetime64):
        return _encode(pd.Timestamp(value))
    if isinstance(value, np.timedelta64):
        return _encode(pd.Timedelta(value))
    if isinstance(value, np.generic):
        if isinstance(value, np.floating) and value.dtype.itemsize > 8:
            raise ValueError("Extended precision floating values are unsupported")
        return _encode(value.item())
    if isinstance(value, bool):
        return {"t": "bool", "v": value}
    if isinstance(value, int):
        return {"t": "int", "v": str(value)}
    if isinstance(value, float):
        return {"t": "float", "v": value.hex()}
    if isinstance(value, Decimal):
        return {"t": "decimal", "v": str(value)}
    if isinstance(value, str):
        return {"t": "str", "v": value}
    if isinstance(value, datetime):
        tz = getattr(value.tzinfo, "key", None) or getattr(value.tzinfo, "zone", None)
        return {"t": "datetime", "v": value.isoformat(), "tz": tz}
    if isinstance(value, date):
        return {"t": "date", "v": value.isoformat()}
    if isinstance(value, timedelta):
        return {"t": "duration", "v": [value.days, value.seconds, value.microseconds]}
    raise ValueError(f"Unsupported dataset cell type: {type(value).__name__}")


def _decode(cell: Any) -> Any:
    import pandas as pd

    if not isinstance(cell, dict) or not isinstance(cell.get("t"), str):
        raise ValueError("Invalid typed dataset cell")
    tag = cell["t"]
    keys = {"t"} if tag in {"none", "na", "nat"} else (
        {"t", "v", "tz"} if tag in {"timestamp", "datetime"} else {"t", "v"})
    if set(cell) != keys:
        raise ValueError("Invalid typed dataset cell fields")
    if tag in {"none", "na", "nat"}:
        return {"none": None, "na": pd.NA, "nat": pd.NaT}[tag]
    value = cell["v"]
    if tag == "bool" and type(value) is bool:
        return value
    if tag == "str" and isinstance(value, str):
        return value
    if tag == "int" and isinstance(value, str) and re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
        return int(value)
    if tag == "float" and isinstance(value, str):
        parsed = float.fromhex(value)
        if parsed.hex() != value:
            raise ValueError("Noncanonical dataset float")
        return parsed
    if tag == "decimal" and isinstance(value, str):
        return Decimal(value)
    if tag == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if tag in {"timestamp", "datetime"} and isinstance(value, str):
        tz = cell["tz"]
        if tz is not None and (not isinstance(tz, str) or
                not re.fullmatch(r"[A-Za-z0-9_/+:. -]{1,128}", tz)):
            raise ValueError("Invalid dataset timezone")
        parsed = pd.Timestamp(value)
        if tz is not None:
            if parsed.tz is None:
                raise ValueError("Dataset timezone requires an aware timestamp")
            parsed = parsed.tz_convert(tz)
        return parsed if tag == "timestamp" else parsed.to_pydatetime(warn=False)
    if tag == "timedelta" and isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        return pd.Timedelta(int(value), unit="ns")
    if tag == "duration" and isinstance(value, list) and len(value) == 3 and all(type(v) is int for v in value):
        return timedelta(days=value[0], seconds=value[1], microseconds=value[2])
    raise ValueError(f"Invalid or unsupported dataset cell tag: {tag}")


def _dtype(dtype: Any) -> dict:
    import pandas as pd

    if isinstance(dtype, pd.CategoricalDtype):
        return {"kind": "category", "ordered": dtype.ordered,
                "categories": [_encode(v) for v in dtype.categories],
                "categories_dtype": _dtype(dtype.categories.dtype)}
    name = str(dtype)
    if isinstance(dtype, pd.StringDtype):
        return {"kind": "string", "storage": dtype.storage}
    if re.fullmatch(r"object|bool|boolean|u?int(8|16|32|64)|float(16|32|64)|[Uu]?Int(8|16|32|64)|Float(32|64)", name):
        return {"kind": "dtype", "name": name}
    if re.fullmatch(r"(datetime64|timedelta64)\[(s|ms|us|ns)\]", name):
        return {"kind": "dtype", "name": name}
    if isinstance(dtype, pd.DatetimeTZDtype):
        return {"kind": "datetime_tz", "unit": dtype.unit, "tz": str(dtype.tz)}
    raise ValueError(f"Unsupported dataset dtype: {name}")


def _restore_dtype(descriptor: Any):
    import pandas as pd

    if not isinstance(descriptor, dict):
        raise ValueError("Invalid dataset dtype descriptor")
    kind = descriptor.get("kind")
    if kind == "dtype" and set(descriptor) == {"kind", "name"}:
        name = descriptor["name"]
        if isinstance(name, str) and (re.fullmatch(
                r"object|bool|boolean|u?int(8|16|32|64)|[Uu]?Int(8|16|32|64)|float(16|32|64)|Float(32|64)", name)
                or re.fullmatch(r"(datetime64|timedelta64)\[(s|ms|us|ns)\]", name)):
            return pd.api.types.pandas_dtype(name)
    if kind == "string" and set(descriptor) == {"kind", "storage"}:
        if descriptor["storage"] in {"python", "pyarrow", "pyarrow_numpy"}:
            return pd.StringDtype(storage=descriptor["storage"])
    if kind == "datetime_tz" and set(descriptor) == {"kind", "unit", "tz"}:
        unit, tz = descriptor["unit"], descriptor["tz"]
        if unit in {"s", "ms", "us", "ns"} and isinstance(tz, str) and re.fullmatch(r"[A-Za-z0-9_/+:. -]{1,128}", tz):
            return pd.DatetimeTZDtype(unit=unit, tz=tz)
    if kind == "category" and set(descriptor) == {"kind", "ordered", "categories", "categories_dtype"}:
        categories = descriptor["categories"]
        if (type(descriptor["ordered"]) is not bool or not isinstance(categories, list)
                or len(categories) > MAX_CELLS or not isinstance(descriptor["categories_dtype"], dict)
                or descriptor["categories_dtype"].get("kind") == "category"):
            raise ValueError("Invalid dataset categories")
        dtype = _restore_dtype(descriptor["categories_dtype"])
        return pd.CategoricalDtype(pd.Index([_decode(v) for v in categories], dtype=dtype),
                                   ordered=descriptor["ordered"])
    raise ValueError("Unsupported dataset dtype descriptor")


def encode_frame(frame) -> dict:
    """Encode a dataframe as restricted JSON without precision-losing coercion."""
    import pandas as pd

    if not isinstance(frame, pd.DataFrame):
        raise ValueError("A pandas DataFrame is required")
    if (not frame.columns.is_unique or any(not isinstance(c, str) for c in frame.columns)
            or isinstance(frame.index, pd.MultiIndex)):
        raise ValueError("Dataset requires unique string columns and a single-level index")
    if len(frame.columns) > MAX_COLUMNS or len(frame) * max(1, len(frame.columns)) > MAX_CELLS:
        raise ValueError("Dataset exceeds codec dimensions")
    index = {"dtype": _dtype(frame.index.dtype), "values": [_encode(v) for v in frame.index],
             "name": _encode(frame.index.name)}
    if isinstance(frame.index, pd.RangeIndex):
        index["range"] = [frame.index.start, frame.index.stop, frame.index.step]
    if isinstance(frame.index, (pd.DatetimeIndex, pd.TimedeltaIndex)) and frame.index.freq is not None:
        index["freq"] = frame.index.freqstr
    return {"format": FORMAT, "version": VERSION, "columns": list(frame.columns),
            "series": [{"dtype": _dtype(frame[c].dtype), "values": [_encode(v) for v in frame[c]]}
                       for c in frame.columns], "index": index}


def decode_frame(payload: dict):
    """Validate a restricted payload and restore its values, index and dtypes."""
    import pandas as pd

    if (not isinstance(payload, dict) or set(payload) != {"format", "version", "columns", "series", "index"}
            or payload["format"] != FORMAT or type(payload["version"]) is not int or payload["version"] != VERSION):
        raise ValueError("Invalid dataset codec format/version")
    columns, series, index = payload["columns"], payload["series"], payload["index"]
    if (not isinstance(columns, list) or len(columns) > MAX_COLUMNS
            or any(not isinstance(c, str) for c in columns) or len(set(columns)) != len(columns)
            or not isinstance(series, list) or len(series) != len(columns)
            or not isinstance(index, dict) or not {"dtype", "values", "name"}.issubset(index)
            or set(index) - {"dtype", "values", "name", "range", "freq"}
            or not isinstance(index["values"], list)):
        raise ValueError("Invalid dataset dimensions/index")
    n = len(index["values"])
    if n * max(1, len(columns)) > MAX_CELLS:
        raise ValueError("Dataset exceeds codec dimensions")
    index_dtype = _restore_dtype(index["dtype"])
    values = [_decode(v) for v in index["values"]]
    restored_index = pd.Index(values, dtype=index_dtype, name=_decode(index["name"]))
    if "range" in index:
        limits = index["range"]
        if not isinstance(limits, list) or len(limits) != 3 or any(type(v) is not int for v in limits) or limits[2] == 0:
            raise ValueError("Invalid dataset range index")
        expected = range(*limits)
        if len(expected) != n or any(a != b for a, b in zip(expected, values)):
            raise ValueError("Dataset range index does not match its values")
        restored_index = pd.RangeIndex(*limits, name=restored_index.name)
    if "freq" in index:
        freq = index["freq"]
        if not isinstance(freq, str) or not re.fullmatch(r"[A-Za-z0-9+:. -]{1,128}", freq):
            raise ValueError("Invalid dataset index frequency")
        if isinstance(restored_index, pd.DatetimeIndex):
            restored_index = pd.DatetimeIndex(restored_index, freq=freq)
        elif isinstance(restored_index, pd.TimedeltaIndex):
            restored_index = pd.TimedeltaIndex(restored_index, freq=freq)
        else:
            raise ValueError("Dataset index frequency requires a temporal index")
    restored = {}
    for column, vector in zip(columns, series):
        if (not isinstance(vector, dict) or set(vector) != {"dtype", "values"}
                or not isinstance(vector["values"], list) or len(vector["values"]) != n):
            raise ValueError("Invalid dataset series")
        dtype = _restore_dtype(vector["dtype"])
        cells = [_decode(v) for v in vector["values"]]
        # Category casting silently maps unknown values to NA; refuse this.
        if isinstance(dtype, pd.CategoricalDtype):
            for cell in cells:
                if cell is not None and cell is not pd.NA and cell is not pd.NaT and not (
                        isinstance(cell, float) and math.isnan(cell)) and cell not in dtype.categories:
                    raise ValueError("Dataset value is absent from its categories")
        try:
            restored[column] = pd.Series(cells, dtype=dtype, index=restored_index)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("Dataset values do not match declared dtype without loss") from exc
    frame = pd.DataFrame(restored, columns=columns, index=restored_index)
    if encode_frame(frame) != payload:
        raise ValueError("Dataset payload cannot round-trip without value/dtype loss")
    return frame
