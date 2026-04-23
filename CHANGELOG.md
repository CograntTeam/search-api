# Changelog

All notable changes to the Cogrant Search API.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/),
and the project uses [Semantic Versioning](https://semver.org/). Breaking
changes bump the major; additive changes bump the minor; fixes bump the
patch. Pre-1.0 versions may still change breaking things within a minor;
the first stable contract will be cut as `1.0.0`.

## [Unreleased]

## [0.1.0] — 2026-04-23

First partner-ready revision. The API surface is now usable end-to-end by
an external partner with a key and no hand-holding.

### Added
- **Endpoints.**
  - `POST /v1/searches` — create an async grant-search job.
  - `GET /v1/searches/{job_id}` — poll job status.
  - `GET /v1/searches/{job_id}/matches` — full match + grant payload once
    the job is `done`.
  - `POST /internal/jobs/{job_id}/complete` — n8n completion callback
    (shared-secret auth, not partner-facing).
  - `GET /health` — liveness probe.
  - `GET /` — partner-facing landing page with quickstart and links to
    Swagger UI.
- **Unified error envelope.** Every error response shares
  `{error: {code, message, request_id, details?}}`. Stable codes:
  `UNAUTHORIZED`, `JOB_NOT_FOUND`, `JOB_NOT_READY`, `JOB_FAILED`,
  `INVALID_REQUEST`, `RATE_LIMITED`, `FORBIDDEN`, `INTERNAL_ERROR`.
- **Request IDs.** `X-Request-Id` is echoed on every response and
  stamped onto every log record via a contextvar. Incoming IDs are
  honoured.
- **Idempotency.** `POST /v1/searches` honours `Idempotency-Key`; replays
  surface via an `Idempotency-Replayed: true` response header.
- **Partner callbacks.** Optional `callback_url` on job creation; the
  gateway POSTs the completed job body fire-and-forget when the run
  finishes.
- **Multi-window rate limiting.** Per-key limits across minute / day /
  week, configured on the `api_keys` Airtable row. 429 responses carry
  `Retry-After` and `X-RateLimit-*` headers; happy-path responses carry
  the headroom headers too.
- **Structured logging.** JSON to stdout, one access line per request
  (skip `/health`), every record tagged with the active `request_id`.
- **OpenAPI polish.** `PartnerApiKey` bearer scheme registered so
  Swagger UI has an Authorize button; every route has a descriptive
  body + error response set; `JobCreate` carries a concrete example.
- **Operational docs.** `OPERATIONS.md` captures log format, envelope,
  rate-limit headers, idempotency contract, deploy quirks.

### Workflow-side
- n8n workflow 1.0 (`T9jDeuvlPenJRo7B`) and 1.1A (`hvBbZKkRwXEv4qJX`)
  wired to stamp `api_job_id` on every Search Match row, so the matches
  endpoint can filter results to the originating search.

### Airtable schema
- `api_keys` gains `rate_limit_per_day` and `rate_limit_per_week` number
  fields (`fldUZmK5uTM1ax4li`, `fldNyJUVETt4Srgl7`).

### Known limits
- In-process rate limiter; single worker only. Redis swap needed before
  scaling to multiple workers.
- Partner keys are still issued by hand against the Airtable table;
  there is no admin API.
- Job storage is Airtable; migrate to Postgres before sustained >50
  req/min.
