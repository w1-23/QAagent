"""Strict, successful-only cached translation shared by all retrieval paths."""
from __future__ import annotations

import hashlib
import logging
import re
import threading

from src.config import settings
from src.core.llm_pool import is_thinking_rejected

logger = logging.getLogger(__name__)
_CACHE: dict[str, str] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 500

_TRANSLATE_PROMPT = (
    "You are a scientific literature retrieval assistant across research disciplines.\n"
    "Translate the user question into standard English scientific terms.\n"
    "Rules:\n"
    "1. Output ONLY the English translation, one line, no preamble, no markdown.\n"
    "2. Preserve all entities: cultivar names, gene IDs, disease names, chemical names.\n"
    "3. Use terminology from the user's actual subject; do not introduce a different\n"
    "   organism, field, application or research goal. Do not expand ambiguous abbreviations.\n"
    "4. Translate native-language prose fully into English, not transliterated prose. Use\n"
    "   established English or Latin scientific names; romanization is for proper names only.\n"
    "5. Preserve exact DOI, accession and gene identifiers; never invent numbers or citations.\n"
    "6. Return an empty response if a faithful English query cannot be produced.\n"
)
_BAD_TRANSLATION_RE = re.compile(
    r"\bplease\s+search\b|\bplease\s+find\b|\bplease\s+look\b|"
    r"\btell\s+me\b|\bfind\s+me\b|\bshow\s+me\b|"
    r"\bsearch\s+for\b|\bi\s+(?:want|need)\s+(?:you\s+)?to\b|"
    r"\blet\s+me\b|\bcan\s+you\b|\bcould\s+you\b",
    re.IGNORECASE,
)


def _is_english(text: str) -> bool:
    from src.retrieval.local_query_language import requires_translation

    return bool(re.search(r"[A-Za-z]", text)) and not requires_translation(text)


def parse_translation(raw: str) -> str | None:
    if not raw or not raw.strip():
        return None
    text = re.sub(r"^```[^\n]*\n?", "", raw.strip())
    text = re.sub(r"\n```", "", text)
    for line in text.splitlines():
        candidate = line.strip().strip('"').strip("'").strip()
        for prefix in ("translation:", "english:", "translated:"):
            if candidate.lower().startswith(prefix):
                candidate = candidate[len(prefix):].strip().strip('"').strip("'").strip()
                break
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate:
            return candidate if _is_english(candidate) else None
    return None


def _gateway_headers(base_url: str) -> dict[str, str]:
    """Use the same gateway routing identity as the configured main transport."""
    from urllib.parse import urlsplit
    host = (urlsplit(base_url or "").hostname or "").lower()
    if host != "opencode.ai" and not host.endswith(".opencode.ai"):
        return {}
    return dict(settings.RESOLVED_PROVIDER_HEADERS)


def _generate_english_translation(text: str) -> str | None:
    if not text or not str(text).strip():
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=settings.RESOLVED_FAST_API_KEY,
            base_url=settings.RESOLVED_FAST_BASE_URL,
            timeout=15,
            max_retries=0,  # Every physical retry is charged explicitly below.
            default_headers=_gateway_headers(settings.RESOLVED_FAST_BASE_URL),
        )
        # 翻译是确定性辅助任务，不需要推理；也不再向 provider 发送
        # reasoning/thinking 关闭参数——部分网关会以 BadRequestError 拒绝，
        # 反而让每一次本地检索都先失败一次再重试。
        off_body = {}
        answer = ""

        def invoke_translation(kwargs):
            from src.core import task_events
            from src.core.tracing import get_job_id, get_session_id
            from src.core.cache_metrics import extract_usage, emit_usage_from_response
            from src.core.cancel import raise_if_cancelled
            raise_if_cancelled("translation:before_attempt")
            job_id = get_job_id()
            remaining = task_events.remaining_seconds(job_id)
            if remaining <= 0:
                raise TimeoutError("translation request deadline exhausted")
            estimate = len(_TRANSLATE_PROMPT + text) // 2 + kwargs["max_tokens"]
            attempt_id = task_events.reserve_attempt(job_id, "query_translation", estimate)
            try:
                response = client.chat.completions.create(**kwargs, timeout=min(15, remaining))
            except BaseException as exc:
                task_events.finish_attempt(attempt_id, error=type(exc).__name__)
                raise
            task_events.finish_attempt(attempt_id, extract_usage(response) or {})
            emit_usage_from_response(get_session_id(), "query_translation", response)
            raise_if_cancelled("translation:after_attempt")
            return response

        for attempt in (1, 2):
            kwargs = {
                "model": settings.RESOLVED_FAST_MODEL,
                "messages": [
                    {"role": "system", "content": _TRANSLATE_PROMPT},
                    {"role": "user", "content": f"User question:\n{text}\n\nEnglish translation:"},
                ],
                "temperature": 0.0,
                "max_tokens": max(64, int(getattr(settings, "HYDE_MAX_TOKENS", 512))),
            }
            if off_body:
                kwargs["extra_body"] = off_body
            try:
                response = invoke_translation(kwargs)
            except Exception as exc:
                if not off_body or not is_thinking_rejected(exc):
                    raise
                logger.warning("Translation provider rejected reasoning flag; retrying without it")
                off_body = {}
                kwargs.pop("extra_body", None)
                response = invoke_translation(kwargs)
            answer = str(response.choices[0].message.content or "").strip()
            if answer:
                break
            logger.warning("Local-query translation returned empty content (attempt %d)", attempt)
        translated = parse_translation(answer)
        if not translated:
            return None
        if _BAD_TRANSLATION_RE.search(translated):
            logger.warning("Local-query translation remained imperative; retrying once")
            retry = invoke_translation(kwargs)
            translated = parse_translation(str(retry.choices[0].message.content or ""))
            if not translated or _BAD_TRANSLATION_RE.search(translated):
                return None
        return translated
    except Exception as exc:  # noqa: BLE001
        logger.warning("Local-query translation failed: %s", type(exc).__name__)
        return None


def cached_translation(text: str) -> str | None:
    """Translate once per distinct input and keep a bounded process-local LRU-ish cache."""
    normalized = str(text or "").strip()
    if not normalized:
        return None
    key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    with _CACHE_LOCK:
        value = _CACHE.get(key)
        if value is not None:
            _CACHE.pop(key, None)
            if _is_english(value) and not _BAD_TRANSLATION_RE.search(value):
                _CACHE[key] = value
                return value
    value = _generate_english_translation(normalized)
    # Validate before admitting to cache; failed/empty/native outputs remain
    # retryable instead of poisoning all later searches for this input.
    if not value or not _is_english(value) or _BAD_TRANSLATION_RE.search(value):
        return None
    with _CACHE_LOCK:
        _CACHE[key] = value
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
    return value
