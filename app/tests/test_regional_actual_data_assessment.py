from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from app.services.regional_actual_assessment import (
    regional_actual_assessment_json,
    regional_actual_assessment_report,
)
from app.services.regional_actual_validation import (
    CandidateTarget,
    SOURCE_CONTRACTS,
    SourceContract,
    validate_candidate_targets,
)
from app.assess_regional_actual_sources import main as assess_sources_cli
from app.services.intensity_units import (
    HISTORICAL_NESO_SOURCE_API,
    LIVE_NESO_SOURCE_API,
    units_for_source_api,
)


START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _contract(
    *, publication_time_verified: bool = True, eligible: bool = True
) -> SourceContract:
    return SourceContract(
        source_id="fixture:verified-regional-actual",
        target_variable="regional_carbon_intensity",
        geography_codes=("NESO-13",),
        region_id=13,
        region_name="London",
        units="gCO2/kWh",
        measurement_classes=("direct_actual",),
        eligible_for_region_13_actual=eligible,
        eligibility_reasons=() if eligible else ("fixture_source_ineligible",),
        publication_time_verified=publication_time_verified,
        official_hosts=("data.example.org",),
    )


def _candidate(
    *,
    source_id: str = "fixture:verified-regional-actual",
    source_record_id: str | int | None = "row-1",
    interval_start: datetime = START,
    value: float = 42.0,
    units: str = "gCO2/kWh",
    measurement_class: str = "direct_actual",
    target_variable: str = "regional_carbon_intensity",
    geography_code: str = "NESO-13",
    region_id: int | None = 13,
    region_name: str | None = "London",
    source_url: str | None = "https://data.example.org/records/row-1",
    retrieved_at: datetime | None = START + timedelta(minutes=40),
    published_at: datetime | None = START + timedelta(minutes=35),
) -> CandidateTarget:
    return CandidateTarget(
        source_id=source_id,
        target_variable=target_variable,
        measurement_class=measurement_class,
        geography_code=geography_code,
        region_id=region_id,
        region_name=region_name,
        interval_start=interval_start,
        interval_end=interval_start + timedelta(minutes=30),
        value=value,
        units=units,
        source_record_id=source_record_id,
        source_url=source_url,
        retrieved_at=retrieved_at,
        published_at=published_at,
    )


def test_source_assessment_is_deterministic_and_separates_regional_from_national():
    first = regional_actual_assessment_json()
    second = regional_actual_assessment_json()
    report = json.loads(first)

    assert first == second
    assert report["pilot_region"] == {
        "region_id": 13,
        "name": "London",
        "dno_name": "UKPN London",
    }
    availability = report["availability"]
    assert availability["direct_regional_actuals"]["available"] is False
    assert availability["credible_regional_estimates"]["available"] is False
    assert availability["national_estimated_actuals"]["available"] is True
    assert report["evaluation_readiness"]["actual_target_evaluation"] is False
    assert report["evaluation_readiness"]["historical_forecast_backtesting"] is False

    sources = {item["source_id"]: item for item in report["sources"]}
    assert sources["neso-regional-carbon-intensity-api"][
        "regional_actual_evaluation"
    ]["eligible"] is False
    assert sources["neso-national-carbon-intensity-api"][
        "measurement_classification"
    ] == "estimated_actual_national"
    ukpn = sources["ukpn-carbon-intensity"]
    assert ukpn["historical_coverage"]["record_count"] == 32462
    assert ukpn["historical_coverage"]["lpn_record_count"] == 0
    assert "per_record_publication_timestamp" in ukpn["missing_or_unknown_metadata"]
    assert ukpn["source_provenance"]["row_identity_field"] is None
    assert sources["neso-regional-carbon-intensity-forecast-datastore"][
        "source_provenance"
    ]["resource_id"] == "c16b0e19-c02a-44a8-ba05-4db2c0545a2a"


def test_source_assessment_cli_json_is_machine_readable_and_stable():
    report = regional_actual_assessment_report()
    source_ids = [item["source_id"] for item in report["sources"]]

    assert source_ids == sorted(source_ids)
    assert all(
        {
            "publisher",
            "documentation",
            "source_provenance",
            "measurement_classification",
            "measurement_details",
            "target_variable_definition",
            "geographic_scope",
            "time_resolution",
            "historical_coverage",
            "publication_and_revision_timestamps",
            "unit_contract",
            "licence",
            "data_quality_limitations",
            "missing_or_unknown_metadata",
            "regional_actual_evaluation",
        }
        <= set(source)
        for source in report["sources"]
    )


def test_assessment_cli_prints_the_pinned_machine_readable_report(capsys):
    assert assess_sources_cli([]) == 0
    output = capsys.readouterr().out

    assert json.loads(output) == regional_actual_assessment_report()
    assert output == regional_actual_assessment_json() + "\n"


def test_candidate_contracts_preserve_existing_source_unit_distinctions():
    assert units_for_source_api(LIVE_NESO_SOURCE_API) == "gCO2e/kWh"
    assert units_for_source_api(HISTORICAL_NESO_SOURCE_API) == "gCO2/kWh"
    assert SOURCE_CONTRACTS["neso-regional-carbon-intensity-api"].units == (
        "gCO2e/kWh"
    )
    assert SOURCE_CONTRACTS[
        "neso-regional-carbon-intensity-forecast-datastore"
    ].units == "gCO2/kWh"


def test_validator_accepts_a_complete_provenanced_candidate_under_an_explicit_contract():
    candidate = _candidate()
    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={_contract().source_id: _contract()},
    )

    assert report.accepted_records == (candidate,)
    assert report.record_decisions[0].eligible_for_region_13_actual is True
    assert report.record_decisions[0].available_as_of_feature is True
    assert report.missing_intervals == ()
    assert report.actual_target_evaluation_ready is True


def test_integer_source_record_ids_remain_valid_provenance():
    candidate = _candidate(source_record_id=501)
    contract = _contract()

    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
    )

    assert report.accepted_records == (candidate,)
    assert report.record_decisions[0].source_record_id == 501


def test_target_can_be_valid_without_publication_time_but_not_as_of_feature_ready():
    contract = _contract(publication_time_verified=False)
    candidate = _candidate(published_at=None)

    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
    )

    decision = report.record_decisions[0]
    assert decision.eligible_for_region_13_actual is True
    assert decision.available_as_of_feature is False
    assert decision.readiness_reasons == ("publication_time_unverified",)


def test_supplied_publication_timestamp_must_be_valid_even_if_source_time_is_unverified():
    contract = _contract(publication_time_verified=False)
    candidate = _candidate(published_at=datetime(2026, 10, 1, 12, 35))

    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
    )

    decision = report.record_decisions[0]
    assert decision.eligible_for_region_13_actual is False
    assert "publication_timezone_missing" in decision.rejection_reasons


def test_known_regional_forecast_source_cannot_be_claimed_as_an_actual_target():
    candidate = _candidate(
        source_id="neso-regional-carbon-intensity-api",
        measurement_class="forecast",
        source_url=(
            "https://api.carbonintensity.org.uk/regional/regionid/13"
        ),
        published_at=None,
    )
    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
    )

    decision = report.record_decisions[0]
    assert decision.eligible_for_region_13_actual is False
    assert "forecast_not_actual" in decision.rejection_reasons
    assert "regional_actual_not_evidenced" in decision.rejection_reasons
    assert report.accepted_records == ()
    assert report.missing_intervals == (START,)


def test_national_actual_and_ukpn_estimate_are_rejected_for_london_target_scope():
    national = _candidate(
        source_id="neso-national-carbon-intensity-api",
        measurement_class="estimated_actual",
        target_variable="national_carbon_intensity",
        geography_code="GB",
        region_id=None,
        region_name=None,
        units="gCO2/kWh",
        source_url="https://api.carbonintensity.org.uk/intensity",
    )
    ukpn = _candidate(
        source_id="ukpn-carbon-intensity",
        measurement_class="derived_estimate",
        geography_code="LPN",
        units="gCO2e/kWh",
        source_url=(
            "https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/"
            "catalog/datasets/ukpn-carbon-intensity/records"
        ),
    )
    report = validate_candidate_targets(
        [national, ukpn],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
    )
    decisions = {decision.source_id: decision for decision in report.record_decisions}

    assert "target_variable_mismatch" in decisions[
        "neso-national-carbon-intensity-api"
    ].rejection_reasons
    assert "geographic_scope_mismatch_national_only" in decisions[
        "neso-national-carbon-intensity-api"
    ].rejection_reasons
    assert "london_area_not_covered" in decisions["ukpn-carbon-intensity"].rejection_reasons
    assert "geographic_scope_mismatch" in decisions["ukpn-carbon-intensity"].rejection_reasons
    assert report.accepted_records == ()


def test_unknown_source_and_missing_provenance_are_explicitly_rejected():
    unknown = _candidate(source_id="unreviewed-source")
    invalid = _candidate(source_record_id=None, source_url=None, retrieved_at=None)
    report = validate_candidate_targets(
        [unknown, invalid],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={_contract().source_id: _contract()},
    )
    decisions = {decision.source_id: decision for decision in report.record_decisions}

    assert "unsupported_source" in decisions["unreviewed-source"].rejection_reasons
    invalid_reasons = decisions[_contract().source_id].rejection_reasons
    assert "source_record_id_missing" in invalid_reasons
    assert "source_url_invalid" in invalid_reasons
    assert "retrieval_timestamp_missing" in invalid_reasons


def test_source_contract_identity_must_match_the_source_registry_key():
    candidate = _candidate(source_id="fixture:alias")
    contract = _contract()
    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={candidate.source_id: contract},
    )

    assert "source_contract_identity_mismatch" in report.record_decisions[
        0
    ].rejection_reasons
    assert report.accepted_records == ()


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (_candidate(units="gCO2e/kWh"), "unit_contract_mismatch"),
        (_candidate(value=-1), "value_not_finite_nonnegative"),
        (_candidate(value=float("inf")), "value_not_finite_nonnegative"),
        (
            _candidate(interval_start=START + timedelta(minutes=5)),
            "interval_not_utc_grid",
        ),
    ],
)
def test_candidate_validation_rejects_bad_units_values_and_intervals(candidate, reason):
    contract = _contract()
    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
    )

    assert reason in report.record_decisions[0].rejection_reasons
    assert report.accepted_records == ()


def test_candidate_validation_requires_timezone_aware_interval_and_provenance():
    contract = _contract()
    candidate = _candidate(interval_start=datetime(2026, 10, 1, 12, 0))
    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
    )

    assert "interval_timezone_missing" in report.record_decisions[0].rejection_reasons
    assert report.accepted_records == ()


def test_candidate_batch_reports_duplicates_revisions_conflicts_and_missing_bins():
    contract = _contract()
    duplicate = _candidate(source_record_id="duplicate")
    unresolved_a = _candidate(
        source_record_id="conflict-a",
        interval_start=START + timedelta(minutes=30),
        value=43,
        retrieved_at=START + timedelta(minutes=65),
        published_at=None,
    )
    unresolved_b = _candidate(
        source_record_id="conflict-b",
        interval_start=START + timedelta(minutes=30),
        value=44,
        retrieved_at=START + timedelta(minutes=65),
        published_at=None,
    )
    revision_a = _candidate(
        source_record_id="revision-a",
        interval_start=START + timedelta(hours=1),
        value=45,
        retrieved_at=START + timedelta(hours=1, minutes=40),
        published_at=START + timedelta(hours=1, minutes=35),
    )
    revision_b = _candidate(
        source_record_id="revision-b",
        interval_start=START + timedelta(hours=1),
        value=46,
        retrieved_at=START + timedelta(hours=2),
        published_at=START + timedelta(hours=1, minutes=45),
    )
    report = validate_candidate_targets(
        [duplicate, unresolved_a, unresolved_b, revision_a, revision_b, duplicate],
        requested_start=START,
        requested_end=START + timedelta(hours=2),
        source_contracts={contract.source_id: contract},
    )

    assert report.duplicate_observation_count == 1
    assert report.conflicting_observation_count == 1
    assert report.revision_count == 1
    assert tuple(
        row.source_record_id
        for row in report.accepted_records
        if row.interval_start == START + timedelta(hours=1)
    ) == ("revision-b",)
    revision_decisions = {
        decision.source_record_id: decision for decision in report.record_decisions
    }
    assert "superseded_revision" in revision_decisions[
        "revision-a"
    ].rejection_reasons
    assert report.missing_intervals == (
        START + timedelta(minutes=30),
        START + timedelta(minutes=90),
    )
    assert len(report.accepted_records) == 2
    assert report.actual_target_evaluation_ready is False
    assert report == validate_candidate_targets(
        list(
            reversed(
                [
                    duplicate,
                    unresolved_a,
                    unresolved_b,
                    revision_a,
                    revision_b,
                    duplicate,
                ]
            )
        ),
        requested_start=START,
        requested_end=START + timedelta(hours=2),
        source_contracts={contract.source_id: contract},
    )


def test_same_value_revisions_keep_the_latest_verified_publication():
    contract = _contract()
    earlier = _candidate(
        source_record_id="same-value-revision-a",
        value=45,
        published_at=START + timedelta(minutes=35),
    )
    latest = _candidate(
        source_record_id="same-value-revision-b",
        value=45,
        published_at=START + timedelta(minutes=36),
    )

    report = validate_candidate_targets(
        [earlier, latest],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
    )

    assert report.accepted_records == (latest,)
    assert report.revision_count == 1
    assert report.duplicate_observation_count == 0
    assert report.actual_target_evaluation_ready is True
    decisions = {decision.source_record_id: decision for decision in report.record_decisions}
    assert "superseded_revision" in decisions[
        "same-value-revision-a"
    ].rejection_reasons


def test_distinct_measurement_semantics_are_not_ordered_as_revisions():
    contract = replace(
        _contract(),
        measurement_classes=("direct_actual", "estimated_actual"),
    )
    measured = _candidate(
        source_record_id="measured-row",
        measurement_class="direct_actual",
        published_at=START + timedelta(minutes=35),
    )
    estimated = _candidate(
        source_record_id="estimated-row",
        measurement_class="estimated_actual",
        published_at=START + timedelta(minutes=36),
    )

    report = validate_candidate_targets(
        [measured, estimated],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
    )

    assert report.accepted_records == ()
    assert report.conflicting_observation_count == 1
    assert all(
        "measurement_semantics_conflict" in decision.rejection_reasons
        for decision in report.record_decisions
    )


def test_validator_rejects_inconsistent_publication_time():
    candidate = _candidate(
        published_at=START + timedelta(minutes=20),
    )
    contract = _contract()
    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
    )

    assert "publication_timestamp_inconsistent" in report.record_decisions[0].rejection_reasons
    assert report.accepted_records == ()
