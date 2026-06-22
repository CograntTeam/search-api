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
from app.services.prompts import build_sanity_check_prompt

logger = logging.getLogger(__name__)


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

    def _generate(self, prompt: str) -> tuple[str, dict[str, int]]:
        from google.genai import types  # lazy import

        resp = self._client().models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        text = resp.text or ""
        meta = getattr(resp, "usage_metadata", None)
        usage = {
            "prompt": getattr(meta, "prompt_token_count", 0) or 0,
            "candidates": getattr(meta, "candidates_token_count", 0) or 0,
            "total": getattr(meta, "total_token_count", 0) or 0,
        }
        return text, usage

    async def sanity_check(
        self,
        *,
        today: str,
        company_description: str,
        grant_name: str,
        grant_description: str,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Run the sanity check; return ``(decision, token_usage)``.

        Retries the model call when the output can't be parsed, mirroring the
        n8n ``Parse Successful?`` loop. Raises :class:`GeminiError` if every
        attempt fails.
        """
        prompt = build_sanity_check_prompt(
            today=today,
            company_description=company_description,
            grant_name=grant_name,
            grant_description=grant_description,
        )
        last_err: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                text, usage = await asyncio.to_thread(self._generate, prompt)
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
