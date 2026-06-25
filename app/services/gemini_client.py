"""Gemini sanity-check client.

Wraps the ``google-genai`` SDK to run the verbatim 1.1A prompt in JSON mode and
return the ``decision`` object. Ports the n8n ``Parse2`` robustness (markdown
stripping, truncated-JSON auto-close, trailing-comma removal) and its
retry-on-parse-failure loop.

The SDK is imported lazily inside :meth:`GeminiClient._client` so the pure
mapping/filter modules (and their unit tests) don't require the package to be
installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from app.config import Settings
from app.services.classification_prompt import build_classification_prompt
from app.services.prompts import (
    build_sanity_check_call_block,
    build_sanity_check_prompt,
    build_sanity_check_static_prefix,
)

logger = logging.getLogger(__name__)

# A sanity-check context cache lives only for the forward-search run that creates
# it (and is deleted when the run ends); this TTL is just a safety net so an
# orphaned cache can't linger if cleanup is missed.
_CACHE_TTL_SECONDS = 1800


class GeminiError(RuntimeError):
    """Raised when the model call fails or its output can't be parsed."""


def _sanitize_and_parse(raw: str) -> Any:
    """Port of the n8n ``sanitizeAndParseAI``: extract the first JSON value,
    auto-close if truncated, drop trailing commas, then parse."""
    start = -1
    depth = 0
    in_string = False
    escaped = False
    stack: list[str] = []
    extracted = ""

    for i, ch in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_string:
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            if depth == 0 and start == -1:
                start = i
            depth += 1
            stack.append(ch)
        elif ch in "}]":
            if depth > 0:
                depth -= 1
                stack.pop()
                if depth == 0 and start != -1:
                    extracted = raw[start : i + 1]
                    break

    if not extracted and start != -1:
        extracted = raw[start:]
        while stack:
            opener = stack.pop()
            extracted += "}" if opener == "{" else "]"
    elif not extracted:
        extracted = raw

    extracted = re.sub(r",\s*([}\]])", r"\1", extracted)
    return json.loads(extracted)


def extract_decision(parsed: Any) -> dict[str, Any]:
    """Pull the ``decision`` object out of the parsed model output.

    The prompt wraps everything under ``{"decision": {...}}``; tolerate a model
    that returns the decision fields at the top level instead.
    """
    if isinstance(parsed, dict):
        inner = parsed.get("decision")
        if isinstance(inner, dict):
            return inner
        return parsed
    raise GeminiError("Model output was not a JSON object")


class GeminiClient:
    def __init__(self, settings: Settings, *, max_attempts: int = 3) -> None:
        self.settings = settings
        self.model = settings.gemini_model
        self.api_key = settings.gemini_api_key
        self.max_attempts = max_attempts
        self._genai_client: Any = None

    def _client(self) -> Any:
        if self._genai_client is None:
            if not self.api_key:
                raise GeminiError("GEMINI_API_KEY is not configured")
            from google import genai  # lazy import

            self._genai_client = genai.Client(api_key=self.api_key)
        return self._genai_client

    def _generate(
        self, prompt: str, *, cached_content: str | None = None
    ) -> tuple[str, dict[str, int]]:
        from google.genai import types  # lazy import

        config = types.GenerateContentConfig(response_mime_type="application/json")
        if cached_content:
            config.cached_content = cached_content
        resp = self._client().models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        text = resp.text or ""
        meta = getattr(resp, "usage_metadata", None)
        usage = {
            "prompt": getattr(meta, "prompt_token_count", 0) or 0,
            "candidates": getattr(meta, "candidates_token_count", 0) or 0,
            "total": getattr(meta, "total_token_count", 0) or 0,
            "cached": getattr(meta, "cached_content_token_count", 0) or 0,
        }
        return text, usage

    async def _run_decision(
        self, prompt: str, *, cached_content: str | None = None
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Generate, parse, and extract the ``decision`` object, retrying on parse
        failure (n8n ``Parse Successful?`` loop). Raises :class:`GeminiError` if
        every attempt fails. ``cached_content`` references a context cache holding
        the static prefix, so ``prompt`` is only the new (per-grant) input."""
        last_err: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                text, usage = await asyncio.to_thread(
                    self._generate, prompt, cached_content=cached_content
                )
                decision = extract_decision(_sanitize_and_parse(text))
                return decision, usage
            except (ValueError, GeminiError) as exc:
                last_err = exc
                logger.warning(
                    "gemini.parse_retry attempt=%s/%s err=%s",
                    attempt,
                    self.max_attempts,
                    exc,
                )
        raise GeminiError(
            f"Could not parse Gemini output after {self.max_attempts} attempts: {last_err}"
        )

    async def sanity_check(
        self,
        *,
        today: str,
        company_description: str,
        grant_name: str,
        grant_description: str,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Run the sanity check on a single combined prompt; return
        ``(decision, token_usage)``.

        Used by the reverse search and as the forward search's fallback when no
        context cache is available.
        """
        prompt = build_sanity_check_prompt(
            today=today,
            company_description=company_description,
            grant_name=grant_name,
            grant_description=grant_description,
        )
        return await self._run_decision(prompt)

    async def sanity_check_cached(
        self,
        *,
        cache_name: str,
        grant_name: str,
        grant_description: str,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Sanity-check one grant against a context cache holding the rubric +
        company (created by :meth:`create_sanity_cache`). Only the per-grant call
        block is sent as new input; the cached prefix supplies the rest, so the
        bulk of the prompt is billed at the reduced cached-input rate."""
        prompt = build_sanity_check_call_block(
            grant_name=grant_name, grant_description=grant_description
        )
        return await self._run_decision(prompt, cached_content=cache_name)

    async def create_sanity_cache(
        self, *, today: str, company_description: str
    ) -> str | None:
        """Create a context cache holding the grant-independent sanity-check prefix
        (rubric + date + company) for one forward-search run; return its cache name.

        Returns ``None`` when caching is unavailable for any reason - no API key, a
        prefix below the model's minimum cacheable size, or any API error - so the
        caller transparently falls back to :meth:`sanity_check`. Caching is a cost
        optimisation and must never fail the search.
        """
        if not self.api_key:
            return None
        prefix = build_sanity_check_static_prefix(
            today=today, company_description=company_description
        )
        try:
            return await asyncio.to_thread(self._create_cache, prefix)
        except Exception as exc:  # noqa: BLE001 - best-effort; degrade to no cache
            logger.warning("gemini.cache_create_failed err=%s", exc)
            return None

    def _create_cache(self, prefix: str) -> str:
        from google.genai import types  # lazy import

        cache = self._client().caches.create(
            model=self.model,
            config=types.CreateCachedContentConfig(
                display_name="sanity-check-prefix",
                contents=[prefix],
                ttl=f"{_CACHE_TTL_SECONDS}s",
            ),
        )
        return cache.name

    async def delete_cache(self, cache_name: str) -> None:
        """Best-effort delete of a run's context cache (it also expires via TTL)."""
        try:
            await asyncio.to_thread(self._client().caches.delete, cache_name)
        except Exception as exc:  # noqa: BLE001 - non-fatal; TTL will reap it
            logger.warning("gemini.cache_delete_failed name=%s err=%s", cache_name, exc)

    async def classify_company(
        self, *, company_description: str, today: str
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Run the company classification (verbatim n8n *AI Agent3* prompt) and
        return ``(classification, token_usage)``.

        Unlike :meth:`sanity_check`, the output is the top-level classification
        object (not wrapped in ``decision``). Retries on parse failure; raises
        :class:`GeminiError` if every attempt fails.
        """
        prompt = build_classification_prompt(
            company_description=company_description, today=today
        )
        last_err: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                text, usage = await asyncio.to_thread(self._generate, prompt)
                parsed = _sanitize_and_parse(text)
                if not isinstance(parsed, dict):
                    raise GeminiError("Classification output was not a JSON object")
                return parsed, usage
            except (ValueError, GeminiError) as exc:
                last_err = exc
                logger.warning(
                    "gemini.classify_retry attempt=%s/%s err=%s",
                    attempt,
                    self.max_attempts,
                    exc,
                )
        raise GeminiError(
            f"Could not parse classification after {self.max_attempts} attempts: {last_err}"
        )
