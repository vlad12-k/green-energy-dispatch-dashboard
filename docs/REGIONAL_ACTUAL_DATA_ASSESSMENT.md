# Regional Actual Data Assessment — London Pilot

**Evidence snapshot:** 11 October 2026
**Pilot:** NESO region 13, London (`UKPN London`)
**Decision:** no defensible London regional historical actual target was verified. No source adapter or database migration is included.

## Findings

The official NESO regional Carbon Intensity API and the NESO regional archive provide region-specific **forecasts**, not verified historical actual targets. National NESO responses do contain `actual` values, but these are GB-wide estimates based on metered generation and are not London observations. UK Power Networks (UKPN) publishes a beta, derived blended-intensity estimate for its observed EPN and SPN areas; the snapshot checked contained no LPN (London) records. Elexon B1610 reports metered energy per Balancing Mechanism Unit in MWh, not regional carbon intensity.

Therefore:

| Availability class for London region 13 | Finding | Eligible as an evaluation target? |
|---|---|---|
| Direct regional historical actual | Not verified in the official sources and samples examined. Regional API samples exposed `forecast` only; the archived regional dataset is explicitly a forecast dataset. | No |
| Credible regional estimate derived from measured inputs | UKPN has a beta derived estimate for EPN/SPN, but no LPN observations were present. Its blended value also uses a NESO regional forecast proxy for transmission imports. | No, on current evidence |
| National actual | NESO national `actual` values are available as estimates from metered generation. | No for region 13; national scope is not spatially interchangeable with London |

This is an evidence-bounded result, not a claim that no future or unexamined regional data source could exist. The source assessment records unknown coverage and metadata explicitly rather than extrapolating from sampled dates.

## Sources investigated

### NESO Carbon Intensity API — regional

- Official documentation: [API definitions](https://carbonintensity.github.io/api-definitions/); [API root](https://api.carbonintensity.org.uk/).
- Direct read-only samples: [London, 1 June 2025](https://api.carbonintensity.org.uk/regional/intensity/2025-06-01T00:00Z/2025-06-02T00:00Z/regionid/13) and [London, 10 October 2026](https://api.carbonintensity.org.uk/regional/intensity/2026-10-10T00:00Z/2026-10-11T00:00Z/regionid/13). Both identify `regionid: 13`, `shortname: London`, `dnoregion: UKPN London`; sampled half-hour rows contain `intensity.forecast` and no `intensity.actual`.
- A 1 October 2017 London query returned no regional rows. This is a sample only; it does not establish a universal start date.
- Geography and resolution: 14 GB DNO regions, including London region 13; half-hour periods. Responses expose period `from`/`to`, not an original forecast issue or revision time.
- The inspected API documentation does not specify a maximum arbitrary historical `from`/`to` range or guarantee the first date of regional history.
- Licence: Carbon Intensity API/site terms state CC BY 4.0. The JSON response does not include a unit field. The public site labels values `gCO2/kWh`, while the existing application maps `neso-carbon-intensity-api` to `gCO2e/kWh` in `app/services/intensity_units.py`. This semantic discrepancy is unresolved: this milestone makes no conversion and does not treat the labels as equivalent.

### NESO Regional Carbon Intensity Forecast archive

- Official [dataset](https://www.neso.energy/data-portal/regional-carbon-intensity-forecast), [methodology](https://www.neso.energy/data-portal/regional-carbon-intensity-forecast/regional_carbon_intensity_forecast_methodology), and [Open Data Licence](https://www.neso.energy/data-portal/ngeso-open-licence).
- The existing PR #8 source investigation records the London field as a modelled forecast in `gCO2/kWh`, with UTC half-hour timestamps, stable source row IDs, no actual values, and no original forecast issue time. That investigation's 11 October 2026 snapshot contained 136,190 rows and ended at `2026-07-24T12:00:00Z`; it is not complete through the snapshot date. This milestone reuses that evidence rather than rebuilding the acquisition path.
- NESO's [FOI/25/161 response](https://www.neso.energy/document/373951/download) states that NESO did not hold the requested historical regional generation-mix dataset. The archive's forecast values therefore cannot be relabelled as regional actuals. Its retrieval timestamp is the actual acquisition time, not an invented historical forecast vintage.
- The source licence requires the attribution “Supported by National Energy SO Open Data”.

### NESO national sources

- [National Carbon Intensity API](https://api.carbonintensity.org.uk/intensity/2026-10-10T00:00Z/2026-10-11T00:00Z): the 10 October 2026 sample contained half-hour `forecast` and `actual` fields. NESO describes national `actual` as an estimate based on metered generation, not a direct measurement of emissions. It is GB-wide and cannot be used as a London target. API rows provide period bounds, but the sampled payload has no original publication or revision timestamp. The site documents historical data from 26 September 2017; the inspected API documentation does not state a maximum date-range size.
- [National Carbon Intensity Forecast dataset](https://www.neso.energy/data-portal/national-carbon-intensity-forecast): national forecast data, reported in `gCO2/kWh`, under the NESO Open Data Licence. A dataset update frequency is not a per-row issue time; forecast vintages were not verified.
- [Historic generation mix](https://www.neso.energy/data-portal/historic-generation-mix): national history documented from 1 January 2009, updated hourly, with seasonal decomposition used to correct missing or irregular points and data subject to cleansing. It is not regionally mapped to London.

### UK Power Networks Carbon Intensity

- Official [dataset and methodology](https://ukpowernetworks.opendatasoft.com/explore/assets/ukpn-carbon-intensity/) and [catalogue API](https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets/ukpn-carbon-intensity).
- The beta method uses PI readings matched to ECR generators over 1 MW, fuel factors and a DNO/GSP calculation. Its blended estimate combines local generation with NESO regional **forecast** intensity as a proxy for transmission imports. It excludes unregistered/unmetered generation, omits network losses and sensitivity factors, assigns batteries 0 `gCO2e/kWh`, and may return null where SGT data are unavailable or exports cannot be attributed.
- The catalogue metadata reports CC BY 4.0. UKPN documents the blended output in `gCO2e/kWh` and input NESO factors in `gCO2/kWh`; the record schema itself has no unit field. No numerical conversion is applied.
- Direct API snapshot on 11 October 2026: 32,462 rows from `2026-09-11T00:00:00Z` to `2026-10-11T16:00:00Z`; EPN 22,626, SPN 9,836, LPN 0. Only 15,775 rows had a non-null blended-intensity value. The API page exposes a dataset-level modification time, not row-level publication/revision times. Interactive viewing asks users to register/login, although the catalogue and bounded read-only records requests used for this assessment succeeded.
- UKPN company coverage includes London, but that does not prove this dataset contains London records: the observed snapshot has no LPN rows. The data are therefore not eligible as a region-13 target on current evidence.

### Elexon BMRS Insights — B1610

- Official [B1610 stream schema](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/B1610/stream), [BMRS data licence](https://www.elexon.co.uk/bsc/data/balancing-mechanism-reporting-agent/copyright-licence-bmrs-data/), and [settlement revision explanation](https://www.elexon.co.uk/bsc/article/bsc-insight-how-to-get-more-detailed-metered-volume-data-from-our-insights-solution/).
- B1610 provides actual metered volume (`quantity`) in MWh per BM Unit and Settlement Period; values may be positive, negative or zero. It is energy, not intensity or instantaneous power. Initial publication is five days after the operational period using the Interim Information run; subsequent Settlement Runs refresh the records. `settlementRunType` is part of the schema, but the documented row has no original publication timestamp.
- BM Units are not DNO regions; the schema provides no London region-13 identity. Location mapping, fuel factors, demand and other data would be needed, and still would not make this source a direct regional-intensity measurement.
- Elexon's licence grants broad use subject to conditions, including the attribution “Contains BMRS data © Elexon Limited copyright and database right [year].”

## Forecast evaluation readiness

- **Actual-target evaluation:** not supported for London region 13 because no eligible region-matched target series was verified.
- **Historical forecast backtesting:** not supported by the archived regional forecasts because original issue times are absent and the prior source snapshot ends months before the evidence date. Retrospectively retrieved forecasts cannot be presented as historically available predictions.
- **As-of feature availability:** not verified for historical regional forecast vintages. Actual-target availability and forecast-vintage availability are separate requirements.
- **Region-matched baseline comparison:** not justified without eligible region-13 targets. No MAE, RMSE or other accuracy metric is calculated or published.

## Validation foundation

`app.services.regional_actual_validation` introduces explicit candidate source contracts and a deterministic validator. It checks source identity and official host, target-variable semantics, region ID/name and geography code, exact source unit, measurement classification, finite non-negative value, timezone-aware UTC half-hour interval, requested window, record ID, URL and retrieval provenance. Publication time is reported separately: an otherwise valid target may be usable as a label while remaining ineligible as an as-of-time feature when its publication time is missing or not verified for that source.

The batch validator retains row-level reason codes; rejects duplicates; distinguishes revisions only when publication timestamps are verified and uniquely ordered; keeps only the latest verified revision in the canonical accepted target series and marks earlier versions as superseded; rejects conflicting unversioned or same-time observations and mixed measurement semantics; and reports every missing half-hour in the requested window. The production source registry currently marks all evaluated sources ineligible for London actual evaluation. The positive validator contract in tests is synthetic and test-only; it is not an assertion that a real source qualifies.

The existing `app.services.historical_dataset.build_dataset` remains authoritative for persisted NESO data, actual/forecast separation, source-specific units, deterministic revision ordering, duplicate/conflict counts and missing intervals. Existing PostgreSQL persistence and offline replay are retained without migrations. No database writes or live source adapters were added.

## Reproducibility

The pinned source assessment is deterministic and makes no network or database calls:

```bash
python -m app.assess_regional_actual_sources
```

To save its JSON output for a review or downstream process:

```bash
python -m app.assess_regional_actual_sources > regional-actual-source-assessment.json
```

Candidate-target validation and source-specific unit assertions are covered by offline tests. Existing PostgreSQL tests use the repository's disposable per-test schema and real Alembic migrations; they do not modify the user's persistent database.

## Remaining limitations

- No verified London regional actual source or region-13 derived estimate is currently eligible.
- The API unit-label discrepancy between the public `gCO2/kWh` label and the existing live-source `gCO2e/kWh` application contract remains unresolved; no conversion is attempted.
- UKPN's source data are beta, short in the observed snapshot, incomplete, forecast-dependent for imports, and absent for LPN at the time checked.
- NESO regional forecast issue times and historical revision history remain unavailable in the evidence examined.
- A future source review should repeat bounded metadata/coverage checks and update the pinned assessment only with new official evidence.
