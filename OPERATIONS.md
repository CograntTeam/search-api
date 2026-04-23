# Operating the Cogrant Search API

Runtime-side notes that don't belong in the README. Focused on what you
need to know when something goes wrong at 2am, not on the architecture.

## Log format

Every log line is one JSON object on stdout, captured by Render. Typical
fields on an access log:

```json
{
  "ts": "2026-04-23T11:32:01,492",
  "logger": "app.access",
  "level": "INFO",
  "message": "http.request method=GET path=/v1/searches/... status=200 duration_ms=84",
  "request_id": "req_7c0a1f9e4b2d88ab"
}
```

`request_id` is stamped onto **every** record emitted while a request is
in flight — including logs from deep inside handlers and background
tasks, via a contextvar. Use it to grep across correlated events:

```bash
# On Render's log viewer
request_id:req_7c0a1f9e4b2d88ab
```

Each response also carries an `X-Request-Id` header echoing the same
value. If a partner reports a failure, ask them to quote it; a single
search turns up the full trace.

## Error envelope

All error responses share the shape:

```json
{
  "error": {
    "code": "JOB_NOT_READY",
    "message": "Job not yet complete.",
    "request_id": "req_...",
    "details": { "status": "running", "hint": "..." }
  }
}
```

`code` is stable — treat it as the machine-readable part. `message` is
freely editable for clarity. `details` is optional and varies per code.

Known codes: `UNAUTHORIZED`, `FORBIDDEN`, `JOB_NOT_FOUND`, `JOB_NOT_READY`,
`JOB_FAILED`, `INVALID_REQUEST`, `RATE_LIMITED`, `INTERNAL_ERROR`.

## Rate limiting

**Two bucket families:**

*General* — applied to every authenticated route (POST, poll GET,
matches GET). Configured on the `api_keys` row via `rate_limit_per_min`,
`rate_limit_per_day`, `rate_limit_per_week`.

*Search-creation* — applied only to `POST /v1/searches`. Configured via
`searches_per_day` and `searches_per_week`. Polling and matches-fetch
do **not** count against these. Keep the search caps tight (the
expensive work lives there) while leaving the general caps generous
enough for unlimited polling.

Blank or 0 disables any individual window.

When any window trips the response is 429 with:

- Header `Retry-After: <seconds>`
- Header `X-RateLimit-Window: <minute|day|week|searches_day|searches_week>`
- Header `X-RateLimit-Limit: <int>`, `X-RateLimit-Remaining: 0`, `X-RateLimit-Reset: <seconds>`
- `error.details.bucket`: `"general"` (implicit/omitted) or `"searches"`
- `error.details.windows` listing remaining budget on every window in that bucket

On the happy path, every auth'd response carries `X-RateLimit-*` headers
showing the bottleneck window. Partners can use those to pace requests.

The limiter is **in-process**, per worker. Render starter plan = one
worker, so this is fine. If we add a second worker or move off Render,
swap `InMemoryRateLimiter` for a Redis-backed equivalent — the public
API doesn't change.

## Idempotency

POST `/v1/searches` honours an `Idempotency-Key` header. A repeat with
the same key from the same partner returns the original job; we also set
`Idempotency-Replayed: true` on the response header so retries are
observable. Keys are not scoped to a time window — they live as long as
the job row does (effectively forever, currently).

## Deploys

Blueprint on Render, triggered by pushes to `main`. Auto-deploy has been
flaky in practice — when a push does not trigger a deploy within ~5
minutes, hit "Manual Deploy → Deploy latest commit" in the Render
dashboard. See commits `16` and `11`'s deploy history for prior instances.

## Common runtime issues

**502 from Render under burst traffic.** Uvicorn's threadpool is blocked
by sync Airtable calls. Not a code bug per se — fix is to spread load
or move hot paths off Airtable. Sustained load will eventually need
Postgres.

**"model not found" in an n8n execution.** Someone used an unlisted
model string in a workflow. Check the model list in the root `CLAUDE.md`
for the canonical set.

**`useradd: input/output error` inside the sandbox during dev.** Cowork
sandbox quirk. Start a new chat or restart the app; state is captured
in HANDOFF.md and CLAUDE.md.

## Airtable budget

Per-base rate limit is 5 req/s. Every authenticated API request hits
Airtable at least once (`find_key_by_hash`). At 60 req/min sustained
we're at 1 req/s on that path — plenty of headroom. Monitor for changes.
