"""Thin client for firing n8n webhooks.

We never wait on n8n synchronously from the partner-facing endpoints — the job
is created in Airtable first, then n8n is fired in the background. n8n does
its long-running work and calls us back at ``/internal/jobs/{id}/complete``.

If the webhook POST itself fails (n8n is down, DNS error, non-2xx), we mark
the job ``failed`` immediately so partners don't poll forever.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# n8n sometimes takes a few seconds to acknowledge a webhook even though the
# actual work is async. Keep the connect/read window tight but generous enough
# that a cold worker doesn't blow up.
_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


class N8nClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _search_url(self) -> str:
        return self.settings.n8n_webhook_url(self.settings.n8n_search_path)

    def _match_a_url(self) -> str:
        return self.settings.n8n_webhook_url(self.settings.n8n_match_check_a_path)

    def _match_b_url(self) -> str:
        return self.settings.n8n_webhook_url(self.settings.n8n_match_check_b_path)

    async def _fire(
        self, *, workflow: str, url: str, job_id: UUID, payload: dict[str, Any]
    ) -> str | None:
        """Common POST-to-n8n helper. Wraps gateway-controlled metadata into
        the ``payload`` dict that n8n's ``Normalize Entry`` node reads from,
        then POSTs to ``url``.

        Partners' payload keys are preserved; gateway keys win on collision so
        a malicious/buggy partner can't spoof them.

        Returns the n8n execution id if one is in the response, else ``None``.
        Raises ``httpx.HTTPError`` on transport failure; caller decides what
        to do with the job row.
        """
        enriched = {
            **payload,
            "api_job_id": str(job_id),
            "callback_url": f"{self.settings.api_base_url.rstrip('/')}/internal/jobs/{job_id}/complete",
            "internal_secret": self.settings.internal_shared_secret,
        }
        body = {"payload": enriched}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            logger.info(
                "n8n.fire workflow=%s job_id=%s url=%s", workflow, job_id, url
            )
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                return None
            # n8n webhooks commonly return {"executionId": "..."} in response
            # mode "responseNode"; otherwise they may return the raw payload.
            if isinstance(data, dict):
                return data.get("executionId") or data.get("execution_id")
            return None

    async def fire_search(
        self, *, job_id: UUID, payload: dict[str, Any]
    ) -> str | None:
        """POST the job to the n8n search workflow (1.0)."""
        return await self._fire(
            workflow="search",
            url=self._search_url(),
            job_id=job_id,
            payload=payload,
        )

    async def fire_match_check_a(
        self, *, job_id: UUID, payload: dict[str, Any]
    ) -> str | None:
        """POST the job to the n8n match-check A workflow (1.1A)."""
        return await self._fire(
            workflow="match_check_a",
            url=self._match_a_url(),
            job_id=job_id,
            payload=payload,
        )

    async def fire_match_check_b(
        self, *, job_id: UUID, payload: dict[str, Any]
    ) -> str | None:
        """POST the job to the n8n match-check B workflow (1.1B)."""
        return await self._fire(
            workflow="match_check_b",
            url=self._match_b_url(),
            job_id=job_id,
            payload=payload,
        )
