# Airtable schema — planner.cogrant.eu API

Reference for the FastAPI gateway's Airtable configuration. All IDs below are
stable and should be loaded into the gateway as environment variables or a
`config.py` constants module — never hard-coded inside endpoint handlers.

## Base

| Name | ID |
|---|---|
| Cogrant | `apphC0wbp5dYfACfb` |

## Table: `api_keys` — `tblfXzQKso559HNlQ`

Partner API keys. Stores only SHA-256 hashes; plaintext keys are surfaced to
partners once at creation time and never persisted.

| Field | ID | Type | Notes |
|---|---|---|---|
| partner_name (primary) | `flduIoESey2I90OIJ` | singleLineText | |
| key_hash | `fldpMQEbtd1Os0rt7` | singleLineText | SHA-256 hex digest, lookup index |
| key_prefix | `fldAJi4AMZDTscaE5` | singleLineText | First 8 chars of plaintext key (debug/ID only) |
| status | `fldD1cAYMgEOSFA6Q` | singleSelect | `active` / `revoked` |
| rate_limit_per_min | `fldIBHQPAHSxKEpGO` | number | Default 60. Blank/0 → no cap on this window. |
| rate_limit_per_day | *add me* | number | Default 500. Blank/0 → no cap on this window. |
| rate_limit_per_week | *add me* | number | Default 2000. Blank/0 → no cap on this window. |
| contact_email | `fldTOLCO1VzAdkWwL` | email | Technical contact at partner |
| notes | `fldpV2Q3PI9nCpmai` | multilineText | Internal notes |
| created_at | `fldol6T0GIwjxyDmq` | dateTime (ISO/24h/utc) | |
| last_used_at | `fldKiGUeCbjaKt0kk` | dateTime (ISO/24h/utc) | Updated on each authenticated request |

## Table: `api_jobs` — `tbl5QazdvtAVbAHZO`

Async job records. One row per partner API request.

| Field | ID | Type | Notes |
|---|---|---|---|
| job_id (primary) | `fld8fupoWbhbPTPm6` | singleLineText | UUIDv4 surfaced to partners |
| api_key | `fldEkEFNX2a5dFXSb` | multipleRecordLinks → `api_keys` | Who made the request |
| workflow_kind | `fldPo8bfflz9rXakC` | singleSelect | `search` / `match_check_a` / `match_check_b` |
| status | `fld26n3Ijzif0b2O7` | singleSelect | `queued` / `running` / `done` / `failed` |
| request_payload | `fldHA121qP83lyo1b` | multilineText | JSON string |
| result | `fld1XyoAAhL2fW5i9` | multilineText | JSON string, populated on completion |
| error | `fldNueEV7nv9VGbJr` | multilineText | Populated if status=failed |
| callback_url | `fld8M5DAy41qBRAUt` | url | Optional partner webhook |
| idempotency_key | `fldURAnEqrQyRb131` | singleLineText | For deduping retries |
| n8n_execution_id | `fldhTttMCZXdZrSHZ` | singleLineText | Correlation with n8n |
| created_at | `fldNYwdyQxQ5xAJd3` | dateTime (ISO/24h/utc) | |
| updated_at | `fldbUCoaMU4hln1GC` | dateTime (ISO/24h/utc) | |
| completed_at | `fld9XARwPVNiu9Ulh` | dateTime (ISO/24h/utc) | Set when status = done or failed |

## Suggested `.env` entries

```
AIRTABLE_BASE_ID=apphC0wbp5dYfACfb
AIRTABLE_API_KEYS_TABLE_ID=tblfXzQKso559HNlQ
AIRTABLE_API_JOBS_TABLE_ID=tbl5QazdvtAVbAHZO
AIRTABLE_PAT=<personal access token with schema.bases:read + data.records:read + data.records:write on the Cogrant base>
```

## Migration note

When we outgrow Airtable (see gateway README for thresholds), these two tables
map cleanly to Postgres tables with the same names. The `api_key` link field
becomes a foreign key `api_key_id uuid references api_keys(id)`. Keep the
`JobRepository` interface abstract in the gateway code so the migration is one
file.
