"""Partner-facing landing page.

Served at ``/`` — what a partner sees when they paste ``api.cogrant.eu``
into a browser. A thin, self-contained HTML page with the minimal things
they need: how to authenticate, a quickstart curl, the error envelope
shape, rate-limit headers, and links to the interactive Swagger UI.

Self-contained by design — no templates, no static files, no external
CSS — so the gateway never depends on a file-server layer for its own
front door. Rendered once at import time and served as a ``str``.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["meta"], include_in_schema=False)


_LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cogrant Search API</title>
  <meta name="description" content="Partner API for Cogrant's grant-search engine.">
  <style>
    :root {
      --fg: #0b1220;
      --muted: #4b5565;
      --accent: #1a56db;
      --bg: #f7f8fa;
      --card: #ffffff;
      --code-bg: #0f172a;
      --code-fg: #e2e8f0;
      --border: #e5e7eb;
    }
    * { box-sizing: border-box; }
    body {
      font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
            Helvetica, Arial, sans-serif;
      color: var(--fg);
      background: var(--bg);
      margin: 0;
      padding: 0;
    }
    main { max-width: 820px; margin: 0 auto; padding: 56px 24px 80px; }
    header { margin-bottom: 40px; }
    header .tag {
      display: inline-block; padding: 4px 10px; border-radius: 999px;
      background: #e6edff; color: var(--accent); font-size: 12px;
      font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
    }
    h1 { font-size: 34px; margin: 12px 0 6px; letter-spacing: -0.02em; }
    header p { color: var(--muted); margin: 0; font-size: 17px; }
    section {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 24px 28px; margin-bottom: 20px;
    }
    h2 {
      font-size: 18px; margin: 0 0 10px; letter-spacing: -0.01em;
    }
    section p { margin: 0 0 10px; color: var(--muted); }
    code, pre {
      font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo,
                   Consolas, "Liberation Mono", monospace;
      font-size: 13.5px;
    }
    p code {
      background: #eef1f6; padding: 1px 6px; border-radius: 4px;
      color: var(--fg);
    }
    pre {
      background: var(--code-bg); color: var(--code-fg);
      padding: 16px 18px; border-radius: 8px; overflow-x: auto;
      line-height: 1.5; margin: 10px 0 0;
    }
    ul { padding-left: 22px; margin: 0; color: var(--muted); }
    li { margin-bottom: 6px; }
    li code {
      background: #eef1f6; padding: 1px 6px; border-radius: 4px;
      color: var(--fg);
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .links { display: flex; gap: 14px; flex-wrap: wrap; }
    .links a {
      display: inline-block; padding: 10px 16px; border-radius: 8px;
      background: var(--accent); color: #fff; font-weight: 600;
    }
    .links a.secondary {
      background: transparent; color: var(--accent);
      border: 1px solid var(--accent);
    }
    footer {
      margin-top: 36px; color: var(--muted); font-size: 13px; text-align: center;
    }
    footer a { color: var(--muted); }
  </style>
</head>
<body>
  <main>
    <header>
      <span class="tag">Cogrant Search API</span>
      <h1>Discover EU grant matches, programmatically.</h1>
      <p>
        A partner-facing API in front of Cogrant's grant-matching engine.
        Submit a client profile; get back the funding calls that actually fit.
      </p>
    </header>

    <section>
      <h2>1&nbsp;&middot;&nbsp;Authenticate</h2>
      <p>
        Every request needs a partner API key passed as
        <code>Authorization: Bearer &lt;key&gt;</code>. Contact Cogrant to get
        one issued.
      </p>
    </section>

    <section>
      <h2>2&nbsp;&middot;&nbsp;Kick off a search</h2>
      <p>
        <code>POST /v1/searches</code> is accepted asynchronously; the response
        returns a <code>job_id</code> you then poll. Two payload shapes are
        supported &mdash; pick one per request.
      </p>
      <p><strong>Existing company</strong> &mdash; if the company already has a
        profile in Cogrant:</p>
<pre>curl -sS -X POST https://api.cogrant.eu/v1/searches \\
  -H 'Authorization: Bearer cog_live_...' \\
  -H 'Content-Type: application/json' \\
  -H 'Idempotency-Key: sprint-42-client-acme' \\
  -d '{"payload": {"company_id": "recABCDEFGHIJKLMN"}}'</pre>
      <p style="margin-top:14px"><strong>New company</strong> &mdash; the
        gateway creates the profile on the fly (Organisation Type is set to
        <code>Private Business</code>) and then runs the search:</p>
<pre>curl -sS -X POST https://api.cogrant.eu/v1/searches \\
  -H 'Authorization: Bearer cog_live_...' \\
  -H 'Content-Type: application/json' \\
  -d '{"payload": {
    "company_name": "Acme Bio",
    "company_description": "Fermentation-based protein for the food industry.",
    "country": "Lithuania",
    "website": "https://acme.bio"
  }}'</pre>
      <p style="margin-top:14px; font-size: 14px">
        <code>company_name</code>, <code>company_description</code>, and
        <code>country</code> are required; <code>website</code> is optional.
        <code>country</code> must match one of the values Cogrant uses on the
        Companies &rarr; Country field.
      </p>
    </section>

    <section>
      <h2>3&nbsp;&middot;&nbsp;Poll for completion</h2>
      <p>
        Searches typically finish in 60&ndash;120&nbsp;seconds. Poll every
        5&ndash;10&nbsp;seconds until <code>status</code> is <code>done</code>
        or <code>failed</code>.
      </p>
<pre>curl -sS https://api.cogrant.eu/v1/searches/{job_id} \\
  -H 'Authorization: Bearer cog_live_...'</pre>
    </section>

    <section>
      <h2>4&nbsp;&middot;&nbsp;Fetch the matches</h2>
      <p>
        Once the job is done, pull the full match set &mdash; parsed match
        analysis plus complete grant-detail JSON for every match.
      </p>
<pre>curl -sS https://api.cogrant.eu/v1/searches/{job_id}/matches \\
  -H 'Authorization: Bearer cog_live_...'</pre>
      <p style="margin-top:14px">
        Each row carries two nested objects:
      </p>
      <ul>
        <li>
          <code>match</code> &mdash; analyst-style decision block (eligibility
          verdict, objective / activity / budget fit, clarification questions,
          consortium expectations).
        </li>
        <li>
          <code>grant</code> &mdash; structured grant metadata
          (<code>core_metadata</code>, <code>timelines</code>,
          <code>financials</code>, <code>eligibility_and_consortia</code>,
          <code>scope_and_activities</code>, <code>scope_batches</code>,
          <code>administrative</code>).
        </li>
      </ul>
      <p style="margin-top:10px">
        Every individual field is documented in the
        <a href="/docs#/searches/get_search_matches_v1_searches__job_id__matches_get">Swagger reference</a>
        &mdash; expand <code>MatchDetails</code> and <code>GrantDetails</code>
        in the schemas panel for the full list with per-field meaning.
      </p>
    </section>

    <section>
      <h2>Errors &amp; rate limits</h2>
      <p>
        Every error response uses a single envelope. <code>error.code</code> is
        machine-readable; <code>error.request_id</code> matches the
        <code>X-Request-Id</code> header and appears in Cogrant's logs &mdash;
        quote it in support requests.
      </p>
<pre>{
  "error": {
    "code": "JOB_NOT_READY",
    "message": "Job not yet complete.",
    "request_id": "req_7c0a1f9e4b2d88ab",
    "details": { "status": "running", "hint": "..." }
  }
}</pre>
      <p style="margin-top:14px">
        Known codes: <code>UNAUTHORIZED</code>, <code>JOB_NOT_FOUND</code>,
        <code>JOB_NOT_READY</code>, <code>JOB_FAILED</code>,
        <code>INVALID_REQUEST</code>, <code>RATE_LIMITED</code>.
      </p>
      <p>
        Every authenticated response carries
        <code>X-RateLimit-Limit</code>, <code>X-RateLimit-Remaining</code>,
        <code>X-RateLimit-Window</code> so you can pace requests. On a 429
        the <code>Retry-After</code> header tells you how long until the
        tripped window frees up.
      </p>
      <p>
        There are <strong>two bucket families</strong>: a general cap on all
        authenticated calls (<code>minute</code> / <code>day</code> /
        <code>week</code>) and a stricter cap on
        <code>POST /v1/searches</code> alone (<code>searches_day</code> /
        <code>searches_week</code>). Polling status and fetching matches
        hit only the general buckets &mdash; feel free to poll freely.
      </p>
    </section>

    <section>
      <h2>Idempotency</h2>
      <p>
        Pass an <code>Idempotency-Key</code> header on
        <code>POST /v1/searches</code> to make calls safely retryable. A
        repeat with the same key returns the original job and sets
        <code>Idempotency-Replayed:&nbsp;true</code>.
      </p>
    </section>

    <section>
      <h2>Interactive reference</h2>
      <p>
        The full OpenAPI schema lives at
        <a href="/openapi.json"><code>/openapi.json</code></a>. A ready-to-try
        Swagger UI is mounted at <a href="/docs"><code>/docs</code></a>
        &mdash; hit the Authorize button and test every endpoint from your
        browser.
      </p>
      <div class="links" style="margin-top:14px">
        <a href="/docs">Open Swagger UI</a>
        <a class="secondary" href="/openapi.json">OpenAPI JSON</a>
      </div>
    </section>

    <footer>
      Questions? Contact <a href="mailto:hello@cogrant.eu">hello@cogrant.eu</a>
      &middot; <a href="https://www.cogrant.eu">cogrant.eu</a>
    </footer>
  </main>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def landing() -> HTMLResponse:
    """Partner-facing landing page. Not part of the API contract."""
    return HTMLResponse(content=_LANDING_HTML)
