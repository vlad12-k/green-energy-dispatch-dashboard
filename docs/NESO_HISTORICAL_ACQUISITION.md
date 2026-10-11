# NESO historical regional forecast acquisition

This note records the official source contract and the bounded acquisition workflow introduced for the London pilot. It deliberately distinguishes an archived forecast target value from an observed/estimated actual and from the time at which the archive was acquired.

## Source investigation

Sources reviewed on 11 October 2026:

- [Official Carbon Intensity API definitions](https://carbon-intensity.github.io/api-definitions/), including `GET /regional/intensity/{from}/{to}/regionid/{regionid}`.
- [NESO Regional Carbon Intensity Forecast dataset](https://www.neso.energy/data-portal/regional-carbon-intensity-forecast) and its public CKAN DataStore SQL endpoint.
- [NESO Regional Forecast Methodology](https://www.neso.energy/data-portal/regional-carbon-intensity-forecast/regional_carbon_intensity_forecast_methodology).
- [NESO Open Licence](https://www.neso.energy/data-portal/ngeso-open-licence).
- NESO's published [FOI response FOI/25/161](https://www.neso.energy/document/373951/download), which says NESO did not hold the requested regional historical generation-mix record.

The API describes regional values as forecasts. A narrow live request for London (region 13), covering 00:00–01:00 UTC on 10 October 2026, returned three half-hour regional entries, each with `intensity.forecast` and no `intensity.actual`. This is a point-in-time contract probe, not evidence that every historical API response has identical fields. The API may show national estimated actuals; those are not London regional actual measurements.

The NESO Data Portal resource is explicitly titled **Regional Carbon Intensity Forecast**. Its DataStore schema describes London values as forecasts predicted using machine-learning models and metered generation, in gCO2/kWh. It has fourteen regional DNO columns, including London, and a UTC timestamp at half-hour resolution; it does not provide actual values or original forecast issue times. The resource also provides stable dataset/resource identifiers and per-row `_id` values, but no forecast-vintage timestamp in the queried records.

At investigation time, the public DataStore contained 136,190 rows; its latest row was `2026-07-24T12:00:00` UTC. A two-hour bounded query returned records for that historical period. The archive therefore has historical *forecast values*, but the several-month gap to the investigation date and the absence of issue-time provenance prevent treating it as a complete recent history or a leakage-safe historical forecast archive. Do not infer forecast accuracy from it.

The selected source is the public DataStore resource because it exposes a genuine region-specific historical forecast series and stable row identifiers. The API regional endpoint was not selected for persistence: the probe yielded forecast values only, and NESO's cited FOI response does not establish a held regional actual-history series. Neither source currently supplies the regional actual targets required for actual-based forecasting evaluation.

## Implemented acquisition contract

Run one explicit UTC range and region per command. The client uses the CKAN `datastore_search_sql` endpoint with an inclusive start and exclusive end, selects only `_id`, `datetime` and the chosen regional forecast column, and limits each invocation to fourteen days (up to 672 half-hour bins). Fourteen days is this application's safety bound, not a claimed NESO portal limit. The response is capped at 1 MB.

The client:

- accepts only region IDs represented by the fourteen regional dataset columns (1–14; the pilot is 13, London);
- requires timezone-aware half-hour-aligned range boundaries and normalises them to UTC;
- uses a one-second minimum request interval, a bounded three-attempt retry policy and a finite timeout;
- retries network errors, HTTP 429 and HTTP 5xx only; honours numeric or HTTP-date `Retry-After`, and aborts without retry if the requested wait exceeds five minutes;
- validates the CKAN success/resource/schema envelope and validates each row's ID, UTC timestamp, requested interval and finite non-negative forecast value;
- leaves missing intervals missing, counts identical duplicate timestamps, rejects every record at conflicting timestamps, and reports row-level rejection reasons;
- records actual count as zero and labels every accepted row `forecast`;
- reports the observation interval separately from the real acquisition time. `original_forecast_issue_at` remains null because the source does not supply it;
- reports both a SHA-256 of canonical JSON (the representation retained by the existing evidence store) and a SHA-256 of the response-body bytes returned by HTTPX. The store does not claim to preserve compressed HTTP wire bytes or headers.

The machine-readable report includes source and resource IDs, exact request URL, units (`gCO2/kWh`), requested and returned ranges, source record IDs, counts, missing intervals, explicit coverage fraction/completeness, duplicate/conflict/rejection details, both hashes, acquisition time, and an explicit `temporal_evaluation_supported: false` reason. The dataset resource does not return a revision identifier in the DataStore query response, so `source_dataset_version` is null rather than invented.

### Read-only default and opt-in persistence

The command prints a JSON report and does not write to PostgreSQL by default:

```bash
python -m app.acquire_neso_historical \
  --region-id 13 \
  --from 2026-07-24T11:00:00Z \
  --to 2026-07-24T12:00:00Z
```

The start is inclusive and the end exclusive. A dry run still performs the requested read-only NESO query; it does not write files or database rows. The 14-day cap is enforced before the request.

To explicitly persist an acquisition, configure `DATABASE_URL` for an already migrated PostgreSQL database and add `--persist`:

```bash
DATABASE_URL='postgresql+psycopg://USER:URL_ENCODED_PASSWORD@HOST:5432/DB' \
  python -m app.acquire_neso_historical \
  --region-id 13 \
  --from 2026-07-24T11:00:00Z \
  --to 2026-07-24T12:00:00Z \
  --persist
```

Persistence uses the existing raw-payload and carbon-observation tables without a migration. The full validated CKAN JSON response is retained as immutable canonical JSON evidence; accepted forecasts are normalized under their real acquisition/retrieval time. `validation_fresh=false` records that the downloaded forecast target is retrospective, while `validation_error=null` means the source response passed this adapter's validation. `intensity_index="not_provided"` is an explicit schema marker because the archive does not supply that API classification. Source-page writes and normalized rows are in one transaction and use existing uniqueness constraints; repeating persistence for the same acquisition is idempotent. A later download is a distinct retrieval event and remains distinct provenance.

Use `python -m app.replay_neso RAW_PAYLOAD_ID --source-record-id SOURCE_ROW_ID` to verify and replay a single archive forecast from a persisted page. Ordinary API raw payloads continue to use the existing one-argument replay command.

No migration, reset, backfill or modification of a persistent database is performed by the implementation or default command. If a configured database is used with `--persist`, the caller is responsible for selecting the intended database and applying reviewed migrations beforehand.

## Evaluation and limitations

This source cannot support genuine regional actual-based evaluation: it contains forecast targets only. It also omits original forecast issue timestamps, so persisted rows have retrieval time equal to when this client actually acquired the archive, not an invented historical publication time. They must not be treated as forecasts available at an earlier historical origin. Existing baselines use accepted actual observations as inputs and therefore do not use these forecast rows as actuals.

The archive was not complete through the investigation date. A requested range may therefore have extensive missing coverage; the report surfaces every missing half-hour and never fills it. The acquisition command does not run a batch backfill, loop across multiple windows, or calculate accuracy metrics. The DataStore endpoint's update cadence is documented as hourly; this does not establish per-row forecast issue times or complete archive coverage.

The dataset page identifies the **NESO Open Licence**. Where data are reproduced or adapted, include the required attribution statement: **“Supported by National Energy SO Open Data”**. The Carbon Intensity API definitions separately state CC BY 4.0 terms. Review the current source-specific licence before redistribution; this project does not imply NESO endorsement.

During this implementation session, no `DATABASE_URL` was configured, so the current user database's migration revision and row counts could not be read. No persistent database was migrated. PostgreSQL integration verification uses the repository's disposable-schema test fixture and real Alembic migrations; migration compatibility is checked without applying changes to the user's configured database.
