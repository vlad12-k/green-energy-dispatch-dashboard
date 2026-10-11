"""Evidence-backed, deterministic assessment of regional actual-data sources."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SourceAssessment:
    source_id: str
    publisher: str
    documentation: tuple[str, ...]
    access: str
    source_provenance: dict[str, Any]
    measurement_classification: str
    measurement_details: str
    target_variable_definition: str
    geographic_scope: str
    time_resolution: str | None
    historical_coverage: dict[str, Any]
    publication_and_revision_timestamps: str
    unit_contract: str | None
    licence: str | None
    data_quality_limitations: tuple[str, ...]
    missing_or_unknown_metadata: tuple[str, ...]
    eligible_for_london_actual_evaluation: bool
    eligibility_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = json.loads(json.dumps(asdict(self), ensure_ascii=False))
        eligible = result.pop("eligible_for_london_actual_evaluation")
        reasons = result.pop("eligibility_reasons")
        result["regional_actual_evaluation"] = {
            "eligible": eligible,
            "decision": "eligible" if eligible else "not_eligible",
            "reasons": reasons,
        }
        return result


REGIONAL_ACTUAL_SOURCE_ASSESSMENTS: tuple[SourceAssessment, ...] = (
    SourceAssessment(
        source_id="neso-historic-generation-mix",
        publisher="National Energy System Operator (NESO)",
        documentation=(
            "https://www.neso.energy/data-portal/historic-generation-mix",
            "https://www.neso.energy/data-portal/ngeso-open-licence",
        ),
        access=(
            "NESO Data Portal download/API; the dataset documents national history "
            "from 1 January 2009 and is updated hourly."
        ),
        source_provenance={
            "portal_slug": "historic-generation-mix",
            "row_identity": None,
        },
        measurement_classification="national_generation_mix_corrected",
        measurement_details=(
            "National generation-mix history; any carbon-intensity output is "
            "GB-wide and corrected/derived, not a London observation."
        ),
        target_variable_definition=(
            "National generation mix and national carbon-intensity history; not "
            "regional carbon-intensity actuals."
        ),
        geographic_scope="Great Britain national system",
        time_resolution="Hourly dataset update; UTC time series fields",
        historical_coverage={
            "start": "2009-01-01",
            "end": None,
            "evidence_as_of": "2026-10-11",
        },
        publication_and_revision_timestamps=(
            "The portal states hourly updates and data cleansing; per-row "
            "publication and revision timestamps are not documented."
        ),
        unit_contract=(
            "Generation fields are reported in MW or percentage as described by "
            "the explorer; no London intensity unit contract exists."
        ),
        licence="NESO Open Data Licence",
        data_quality_limitations=(
            "National scope cannot be relabelled as region 13.",
            "NESO documents seasonal decomposition to correct missing or irregular points.",
            "The published data may change through data cleansing.",
        ),
        missing_or_unknown_metadata=(
            "per_row_publication_timestamp",
            "per_row_revision_history",
            "regional_mapping",
            "coverage_end_in_metadata",
        ),
        eligible_for_london_actual_evaluation=False,
        eligibility_reasons=(
            "geographic_scope_mismatch_national_only",
            "does_not_provide_london_region_13_targets",
        ),
    ),
    SourceAssessment(
        source_id="neso-national-carbon-intensity-api",
        publisher="NESO Carbon Intensity API",
        documentation=(
            "https://api.carbonintensity.org.uk/",
            "https://carbonintensity.github.io/api-definitions/",
            "https://carbonintensity.org.uk/",
        ),
        access=(
            "Public REST endpoint GET /intensity/{from}/{to}; the 10 October 2026 "
            "one-day response contained national half-hour forecast and actual fields."
        ),
        source_provenance={
            "endpoint_template": "/intensity/{from}/{to}",
            "response_fields": ["from", "to", "intensity.forecast", "intensity.actual"],
            "geographic_path": "national",
        },
        measurement_classification="estimated_actual_national",
        measurement_details=(
            "National estimated actual plus forecast; the site describes actual "
            "as estimated from metered generation, not direct emissions metering."
        ),
        target_variable_definition=(
            "GB-wide carbon-intensity estimate for a national half-hour; not a "
            "London regional target."
        ),
        geographic_scope="Great Britain national system",
        time_resolution="30 minutes",
        historical_coverage={
            "verified_sample": "2026-10-10T00:00:00Z/2026-10-11T00:00:00Z",
            "official_site_start": "2017-09-26",
            "api_maximum_range": None,
        },
        publication_and_revision_timestamps=(
            "API samples expose period from/to only; no per-row original "
            "publication, revision or as-of timestamp was present."
        ),
        unit_contract=(
            "The public site labels values gCO2/kWh and the JSON response has no "
            "unit field. The existing application maps neso-carbon-intensity-api "
            "to gCO2e/kWh; this semantic discrepancy is unresolved. No conversion "
            "or equivalence is assumed."
        ),
        licence="CC BY 4.0 (Carbon Intensity API/site terms)",
        data_quality_limitations=(
            "National values are not spatially representative London measurements.",
            "Actual is an estimate based on metered generation, not direct emissions metering.",
            "The sampled API response has no publication/revision vintage fields.",
            "The API documentation inspected did not specify a maximum historical query range.",
        ),
        missing_or_unknown_metadata=(
            "per_row_publication_timestamp",
            "per_row_revision_history",
            "api_maximum_historical_range",
            "explicit_unit_in_response_schema",
        ),
        eligible_for_london_actual_evaluation=False,
        eligibility_reasons=(
            "geographic_scope_mismatch_national_only",
            "national_estimated_actual_is_not_a_regional_measurement",
            "unit_semantics_require_source_owner_clarification",
        ),
    ),
    SourceAssessment(
        source_id="neso-national-carbon-intensity-forecast",
        publisher="National Energy System Operator (NESO)",
        documentation=(
            "https://www.neso.energy/data-portal/national-carbon-intensity-forecast",
            "https://www.neso.energy/data-portal/ngeso-open-licence",
        ),
        access=(
            "NESO Data Portal dataset download/API; national forecast dataset, "
            "updated hourly."
        ),
        source_provenance={
            "portal_slug": "national-carbon-intensity-forecast",
            "dataset_id": None,
            "resource_id": None,
        },
        measurement_classification="forecast_national",
        measurement_details="National forecast, not an actual target.",
        target_variable_definition="GB-wide national carbon-intensity forecast.",
        geographic_scope="Great Britain national system",
        time_resolution="30-minute forecast periods; dataset updated hourly",
        historical_coverage={
            "verified_sample": None,
            "documented_start": None,
            "documented_end": None,
        },
        publication_and_revision_timestamps=(
            "Dataset-level update frequency is hourly; original issue time and "
            "per-row forecast revision history were not verified."
        ),
        unit_contract="NESO methodology reports carbon-intensity values in gCO2/kWh.",
        licence="NESO Open Data Licence",
        data_quality_limitations=(
            "National geography is not London region 13.",
            "Retrospective forecast rows cannot be treated as forecasts available at historical origins without issue-time evidence.",
        ),
        missing_or_unknown_metadata=(
            "historical_coverage_start_and_end",
            "per_row_forecast_issue_timestamp",
            "per_row_revision_history",
        ),
        eligible_for_london_actual_evaluation=False,
        eligibility_reasons=(
            "forecast_not_actual",
            "geographic_scope_mismatch_national_only",
            "forecast_vintage_not_verified",
        ),
    ),
    SourceAssessment(
        source_id="neso-regional-carbon-intensity-api",
        publisher="NESO Carbon Intensity API",
        documentation=(
            "https://api.carbonintensity.org.uk/",
            "https://carbonintensity.github.io/api-definitions/",
            "https://api.carbonintensity.org.uk/regional/intensity/2025-06-01T00:00Z/2025-06-02T00:00Z/regionid/13",
            "https://api.carbonintensity.org.uk/regional/intensity/2026-10-10T00:00Z/2026-10-11T00:00Z/regionid/13",
            "https://www.neso.energy/document/373951/download",
        ),
        access=(
            "Public regional date-range endpoint GET /regional/intensity/{from}/{to}/regionid/{regionid}; "
            "region 13 is London (UKPN London)."
        ),
        source_provenance={
            "endpoint_template": "/regional/intensity/{from}/{to}/regionid/{regionid}",
            "region_id": 13,
            "response_fields": ["from", "to", "intensity.forecast", "generationmix"],
        },
        measurement_classification="forecast_regional",
        measurement_details=(
            "Regional responses are forecasts in the official source description "
            "and in both sampled historical responses; no regional actual value "
            "was observed."
        ),
        target_variable_definition=(
            "Modelled half-hour regional carbon-intensity forecast for a DNO region; "
            "not a directly measured emissions value."
        ),
        geographic_scope="14 GB DNO regions; region 13 is London / UKPN London",
        time_resolution="30 minutes; response period timestamps are UTC",
        historical_coverage={
            "verified_forecast_windows": [
                "2025-06-01T00:00:00Z/2025-06-02T00:00:00Z",
                "2026-10-10T00:00:00Z/2026-10-11T00:00:00Z",
            ],
            "2017_probe": "2017-10-01T00:00:00Z/2017-10-02T00:00:00Z returned no regional records",
            "earliest_regional_history": None,
        },
        publication_and_revision_timestamps=(
            "Responses expose from/to interval times, but no original forecast "
            "issue time or revision timestamp. The 2017 probe returned an empty "
            "data array; it does not establish a universal first-available date."
        ),
        unit_contract=(
            "NESO's public site labels carbon intensity gCO2/kWh and the sampled "
            "regional JSON contains no unit field. The existing application source "
            "contract is gCO2e/kWh. This discrepancy is unresolved and no conversion "
            "is made."
        ),
        licence="CC BY 4.0 (Carbon Intensity API/site terms)",
        data_quality_limitations=(
            "Both sampled historical London windows returned forecast fields only, with no intensity.actual.",
            "NESO FOI response FOI/25/161 says it did not hold the requested historical regional generation-mix dataset.",
            "No regional forecast issue-time or revision provenance was present in the sampled responses.",
            "The inspected API documentation did not state the maximum arbitrary from/to range or a guaranteed regional history start.",
        ),
        missing_or_unknown_metadata=(
            "verified_historical_regional_actuals",
            "earliest_regional_history_date",
            "per_row_forecast_issue_timestamp",
            "per_row_revision_history",
            "api_maximum_historical_range",
            "explicit_unit_in_response_schema",
        ),
        eligible_for_london_actual_evaluation=False,
        eligibility_reasons=(
            "regional_actual_not_evidenced",
            "sampled_regional_records_are_forecasts",
            "forecast_vintage_not_verified",
            "unit_semantics_require_source_owner_clarification",
        ),
    ),
    SourceAssessment(
        source_id="neso-regional-carbon-intensity-forecast-datastore",
        publisher="National Energy System Operator (NESO)",
        documentation=(
            "https://www.neso.energy/data-portal/regional-carbon-intensity-forecast",
            "https://www.neso.energy/data-portal/regional-carbon-intensity-forecast/regional_carbon_intensity_forecast_methodology",
            "https://www.neso.energy/data-portal/ngeso-open-licence",
            "https://www.neso.energy/document/373951/download",
        ),
        access=(
            "Public CKAN DataStore SQL endpoint; existing acquisition reads one "
            "region and a bounded UTC window without database writes by default."
        ),
        source_provenance={
            "dataset_id": "bf3d723b-b4ae-4ec7-9fc9-e584c651a9da",
            "resource_id": "c16b0e19-c02a-44a8-ba05-4db2c0545a2a",
            "region_column": "London",
            "row_identity_field": "_id",
        },
        measurement_classification="forecast_regional_archived",
        measurement_details="Archived modelled forecasts only; no actual values.",
        target_variable_definition=(
            "Regional forecast carbon intensity generated from forecast demand, "
            "generation and network power-flow modelling."
        ),
        geographic_scope="14 GB DNO regions, including London region 13",
        time_resolution="30-minute UTC intervals; dataset update cadence documented as hourly",
        historical_coverage={
            "snapshot_date": "2026-10-11",
            "record_count": 136190,
            "latest_record": "2026-07-24T12:00:00Z",
            "earliest_record": None,
        },
        publication_and_revision_timestamps=(
            "Rows expose observation interval and stable source row IDs, but no "
            "forecast issue timestamp or source revision identifier. Retrieval "
            "time records when the archive was acquired, not original publication."
        ),
        unit_contract="gCO2/kWh (NESO regional archive source contract).",
        licence="NESO Open Data Licence; required attribution: Supported by National Energy SO Open Data",
        data_quality_limitations=(
            "Forecast values are not actual targets.",
            "The existing 11 October 2026 source snapshot ended on 24 July 2026 and is not complete through the assessment date.",
            "Regional method descriptions differ on forecast horizon; this does not establish forecast vintage availability.",
            "NESO reports that historical regional generation mix used to calculate these forecasts is not held as a separate dataset.",
        ),
        missing_or_unknown_metadata=(
            "earliest_archive_record",
            "per_row_forecast_issue_timestamp",
            "per_row_revision_identifier",
            "historical_regional_generation_mix",
        ),
        eligible_for_london_actual_evaluation=False,
        eligibility_reasons=(
            "forecast_not_actual",
            "forecast_vintage_not_verified",
            "archive_incomplete_through_assessment_date",
        ),
    ),
    SourceAssessment(
        source_id="ukpn-carbon-intensity",
        publisher="UK Power Networks",
        documentation=(
            "https://ukpowernetworks.opendatasoft.com/explore/assets/ukpn-carbon-intensity/",
            "https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets/ukpn-carbon-intensity",
            "https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets/ukpn-carbon-intensity/records?where=licence_area%3D%22LPN%22&limit=1",
        ),
        access=(
            "Open Data portal exposes a catalog and records API; direct read-only "
            "API queries succeeded in the assessment snapshot. Portal page also "
            "states registration/login is required to view data interactively."
        ),
        source_provenance={
            "dataset_id": "ukpn-carbon-intensity",
            "catalog_uid": "da_cf522k",
            "row_identity_field": None,
            "record_fields": [
                "half_hour_period",
                "gsp_name",
                "gsp_fl",
                "dno_generation_kwh",
                "neso_intensity_forecast",
                "blended_carbon_intensity",
                "licence_area",
                "licence_area_average_carbon_intensity",
            ],
        },
        measurement_classification="derived_estimate_regional_beta",
        measurement_details=(
            "Beta derived blended estimate, not a direct measurement. It combines "
            "registered embedded-generation PI data with a NESO regional forecast "
            "proxy for transmission imports."
        ),
        target_variable_definition=(
            "GSP/DNO half-hour blended carbon-intensity estimate, with null values "
            "when required SGT/export data cannot be attributed."
        ),
        geographic_scope=(
            "Observed records cover UKPN licence areas EPN and SPN; the portal "
            "methodology does not establish LPN records."
        ),
        time_resolution="30 minutes",
        historical_coverage={
            "snapshot_date": "2026-10-11",
            "record_count": 32462,
            "coverage_start": "2026-09-11T00:00:00Z",
            "coverage_end": "2026-10-11T16:00:00Z",
            "epn_record_count": 22626,
            "spn_record_count": 9836,
            "lpn_record_count": 0,
            "non_null_blended_intensity_count": 15775,
        },
        publication_and_revision_timestamps=(
            "The portal exposes a dataset-level modification time (2026-10-11T16:15:16Z) "
            "but no per-record publication or revision timestamp. half_hour_period is "
            "the observation interval, not an issue time."
        ),
        unit_contract=(
            "UKPN describes the blended output in gCO2e/kWh, while its imported "
            "NESO factor is documented as gCO2/kWh; dataset record fields have no "
            "unit metadata. Keep these labels distinct; no conversion is applied."
        ),
        licence="CC BY 4.0 (dataset catalogue metadata)",
        data_quality_limitations=(
            "Beta methodology only observes registered ECR generators over 1 MW matched to PI tags; unregistered/unmetered generation is omitted.",
            "Transmission imports use a regional NESO forecast proxy, not GSP-specific actual intensity.",
            "Network losses and sensitivity factors are not applied; battery intensity is assumed to be 0 gCO2e/kWh.",
            "The blended value is null where SGT data are absent or exports cannot be reliably attributed; only 15,775 of 32,462 snapshot rows had a non-null blended value.",
            "Observed records cover EPN and SPN only; LPN (London) query returned zero records.",
        ),
        missing_or_unknown_metadata=(
            "lpn_record_values",
            "per_record_publication_timestamp",
            "per_record_revision_history",
            "historical_coverage_before_2026-09-11",
            "explicit_units_in_record_schema",
        ),
        eligible_for_london_actual_evaluation=False,
        eligibility_reasons=(
            "london_lpn_records_absent_in_observed_snapshot",
            "derived_beta_estimate_not_direct_actual",
            "estimate_uses_neso_regional_forecast_input",
            "coverage_is_only_about_one_month",
            "publication_time_not_available_per_record",
        ),
    ),
    SourceAssessment(
        source_id="elexon-bmrs-b1610",
        publisher="Elexon Limited",
        documentation=(
            "https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/B1610/stream",
            "https://www.elexon.co.uk/bsc/data/balancing-mechanism-reporting-agent/copyright-licence-bmrs-data/",
            "https://www.elexon.co.uk/bsc/article/bsc-insight-how-to-get-more-detailed-metered-volume-data-from-our-insights-solution/",
        ),
        access=(
            "BMRS Insights REST stream GET /datasets/B1610/stream; period, run type "
            "and BM Unit filters are documented."
        ),
        source_provenance={
            "dataset_code": "B1610",
            "endpoint": "/datasets/B1610/stream",
            "series_key": ["bmUnit", "settlementDate", "settlementPeriod", "settlementRunType"],
            "record_fields": [
                "dataset",
                "psrType",
                "bmUnit",
                "nationalGridBmUnitId",
                "settlementDate",
                "settlementPeriod",
                "halfHourEndTime",
                "settlementRunType",
                "quantity",
            ],
        },
        measurement_classification="metered_energy_actual_per_bmu",
        measurement_details=(
            "Actual metered energy volume per Balancing Mechanism Unit (BMU), "
            "reissued in subsequent settlement runs; not carbon intensity."
        ),
        target_variable_definition="Metered energy volume per settlement period, quantity in MWh.",
        geographic_scope=(
            "National BM Units; no DNO-region or London region 13 field in the "
            "documented record schema."
        ),
        time_resolution="Settlement Period (normally 30 minutes; API permits periods 1-50)",
        historical_coverage={
            "documented_example": "2022-08-12T00:00:00Z/2022-08-14T00:00:00Z",
            "dataset_start": None,
            "dataset_end": None,
        },
        publication_and_revision_timestamps=(
            "Initial data is published five days after the operational period based "
            "on the Interim Information run, then refreshed by subsequent runs. "
            "settlementRunType identifies the revision family; a per-row original "
            "publication timestamp is not in the documented response schema."
        ),
        unit_contract="MWh per BMU settlement period; values may be positive, negative or zero.",
        licence=(
            "Elexon BMRS Data Licence: worldwide, royalty-free, perpetual, "
            "non-exclusive; attribution required."
        ),
        data_quality_limitations=(
            "BM Units are not DNO regions and require a separate validated location mapping.",
            "Metered energy alone provides neither regional carbon intensity nor complete regional demand/generation coverage.",
            "Settlement runs revise historical values; analyses must pin the run/vintage.",
        ),
        missing_or_unknown_metadata=(
            "dataset_start_and_end",
            "per_row_original_publication_timestamp",
            "verified_bmu_to_london_region_mapping",
            "carbon_intensity_factor_and_methodology",
        ),
        eligible_for_london_actual_evaluation=False,
        eligibility_reasons=(
            "target_variable_is_energy_not_carbon_intensity",
            "geography_is_bmu_not_london_region_13",
            "unit_contract_is_mwh_not_carbon_intensity",
        ),
    ),
)


def regional_actual_assessment_report() -> dict[str, Any]:
    """Return the pinned source findings without network or database access."""
    sources = sorted(
        REGIONAL_ACTUAL_SOURCE_ASSESSMENTS, key=lambda source: source.source_id
    )
    return {
        "assessment_version": 1,
        "evidence_snapshot_date": "2026-10-11",
        "pilot_region": {
            "region_id": 13,
            "name": "London",
            "dno_name": "UKPN London",
        },
        "availability": {
            "direct_regional_actuals": {
                "available": False,
                "classification": "not_verified",
                "evidence": (
                    "NESO regional responses sampled for 2025-06-01 and 2026-10-10 "
                    "contained intensity.forecast only; the official regional archive "
                    "is a forecast dataset."
                ),
            },
            "credible_regional_estimates": {
                "available": False,
                "classification": "candidate_estimate_not_eligible_for_london",
                "evidence": (
                    "UKPN publishes a beta derived blended estimate for EPN/SPN; "
                    "the observed catalogue snapshot had zero LPN records and uses "
                    "NESO regional forecasts for imported intensity."
                ),
            },
            "national_estimated_actuals": {
                "available": True,
                "classification": "national_estimated_actual_only",
                "evidence": (
                    "The national API response for 2026-10-10 contained actual fields; "
                    "NESO describes these as estimates from metered generation."
                ),
                "eligible_for_region_13": False,
            },
        },
        "evaluation_readiness": {
            "actual_target_evaluation": False,
            "historical_forecast_backtesting": False,
            "as_of_time_feature_availability": False,
            "region_matched": False,
            "baseline_comparison": False,
            "mae_or_rmse_calculated": False,
            "forecast_vintages_verified": False,
            "reasons": [
                "No eligible London region 13 actual or documented estimate source was verified.",
                "The regional archive contains forecasts, not actual targets, and omits original issue times.",
                "National actuals are GB-wide and cannot be substituted for London targets.",
                "UKPN LPN records were absent in the observed snapshot; UKPN blended estimates also use a forecast proxy.",
            ],
        },
        "validation_foundation": {
            "candidate_source_gate": "app.services.regional_actual_validation",
            "existing_interval_quality_pipeline": "app.services.historical_dataset.build_dataset",
            "existing_interval_quality_checks": [
                "UTC interval alignment",
                "actual/forecast separation",
                "source-specific unit contracts",
                "finite non-negative values",
                "duplicate records",
                "conflicting revisions",
                "missing intervals",
            ],
            "schema_migration_required": False,
            "source_adapter_implemented": False,
            "source_adapter_decision": "No verified suitable London actual source; do not add an ingestion adapter.",
            "database_writes_performed": False,
        },
        "sources": [source.to_dict() for source in sources],
    }


def regional_actual_assessment_json() -> str:
    """Return a stable JSON representation suitable for automation and review."""
    return json.dumps(
        regional_actual_assessment_report(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
