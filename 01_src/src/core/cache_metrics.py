"""Per-call provider usage. Unknown cache dimensions stay unknown.

Read, creation/write and uncached input have different billing semantics. Only
explicit provider fields are normalized; input-minus-read is never inferred.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping

logger = logging.getLogger(__name__)


def _mapping(value) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    return {}


def _count(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number >= 0 and number.is_integer() else None
    except (TypeError, ValueError, OverflowError):
        return None


def _first_present(containers, names):
    for path, container in containers:
        for name in names:
            value = _count(container.get(name))
            if value is not None:
                return value, path + "." + name
    return None, None


def _numeric_fields(data):
    """Keep counters only, never arbitrary strings from response metadata."""
    out = {}
    for key, value in data.items():
        nested = _mapping(value)
        if nested:
            counts = _numeric_fields(nested)
            if counts:
                out[str(key)] = counts
        elif _count(value) is not None:
            out[str(key)] = _count(value)
    return out


def extract_usage(response) -> dict | None:
    """Normalize one response/SDK usage object without inventing cache counts."""
    try:
        response_map = _mapping(response)
        um = _mapping(getattr(response, "usage_metadata", None)
                      or response_map.get("usage_metadata"))
        rm = _mapping(getattr(response, "response_metadata", None)
                      or response_map.get("response_metadata"))
        raw = _mapping(rm.get("token_usage") or rm.get("usage")
                       or response_map.get("token_usage") or response_map.get("usage"))
        sdk_usage = _mapping(getattr(response, "usage", None) or response_map.get("usage"))
        raw.update(sdk_usage)
        # Accept a bare provider usage object as well as a complete response.
        if not raw and not um and any(k in response_map for k in
                ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens")):
            raw = response_map
        primary = [("usage_metadata", um), ("usage", raw)]
        containers = [("usage", raw), ("usage_metadata", um)]
        for path, container in list(containers):
            for key in ("prompt_tokens_details", "input_token_details", "prompt_token_details"):
                nested = _mapping(container.get(key))
                if nested:
                    containers.append((path + "." + key, nested))
        input_tokens, _ = _first_present(primary, ("input_tokens", "prompt_tokens"))
        output_tokens, _ = _first_present(primary, ("output_tokens", "completion_tokens"))
        total_tokens, total_source = _first_present(primary, ("total_tokens",))
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
            total_source = "derived:input_tokens+output_tokens"
        read, read_source = _first_present(containers, (
            "cached_tokens", "prompt_cache_hit_tokens", "cache_read_input_tokens",
            "cache_read_tokens", "cache_read", "cache_hit"))
        write, write_source = _first_present(containers, (
            "cache_creation_input_tokens", "cache_creation_tokens", "cache_write_input_tokens",
            "cache_write_tokens", "cache_creation", "cache_write"))
        uncached, uncached_source = _first_present(containers, (
            "prompt_cache_miss_tokens", "uncached_tokens", "cache_uncached_tokens",
            "cache_uncached", "cache_miss"))
        if all(value is None for value in (input_tokens, output_tokens, total_tokens, read, write, uncached)):
            return None
        known = sum(value is not None for value in (read, write, uncached))
        status = "reported" if known == 3 else "partial" if known else "unavailable"
        ratio = (round(read / (read + uncached), 4)
                 if read is not None and uncached is not None and read + uncached > 0 else None)
        return {
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": total_tokens, "total_tokens_source": total_source,
            "cache_read": read, "cache_write": write, "cache_uncached": uncached,
            "cache_hit": read, "cache_miss": uncached,  # compatibility aliases
            "cache_status": status, "cache_ratio": ratio,
            "cache_ratio_basis": "reported_read/(reported_read+reported_uncached)" if ratio is not None else None,
            "cache_sources": {"read": read_source, "write": write_source, "uncached": uncached_source},
            "raw_usage_counts": {"usage": _numeric_fields(raw), "usage_metadata": _numeric_fields(um)},
        }
    except Exception as exc:
        logger.debug("[CacheMetrics] extract_usage failed: %s", type(exc).__name__)
        return None


def reset_cache_stats() -> None:
    """Compatibility shim: no process-level pseudo task totals are maintained."""


def emit_usage_from_response(session_id: str, source: str, response, **identity) -> dict | None:
    """Emit a single measured call, preserving unknown dimensions and identity."""
    try:
        merged = extract_usage(response)
        if merged is None:
            return None
        from src.core.progress_bus import emit_usage_delta
        emit_usage_delta(session_id, source, **merged, **identity)
        return merged
    except Exception as exc:
        logger.debug("[CacheMetrics] emit failed: %s", type(exc).__name__)
        return None
