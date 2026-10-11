from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.assess_regional_actual_sources import main as assess_sources_cli
from app.services.intensity_units import (
    HISTORICAL_NESO_SOURCE_API,
    LIVE_NESO_SOURCE_API,
    units_for_source_api,
)
from app.services.neso_carbon_intensity import parse_regional_response
from app.services.regional_actual_assessment import (
    regional_actual_assessment_json,
    regional_actual_assessment_report,
)
from app.services.regional_actual_validation import (
    SOURCE_CONTRACTS,
    CandidateTarget,
    SourceContract,
    validate_candidate_targets,
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

    manifest = report["evidence_manifest"]
    observations = {item["observation_id"]: item for item in manifest["observations"]}
    assert observations["neso-regional-london-2026-10-10"]["snapshot"][
        "actual_rows"
    ] == 0
    assert observations["neso-national-2026-10-10"]["snapshot"][
        "actual_rows"
    ] == 49
    assert observations["ukpn-carbon-intensity-lpn-count"]["snapshot"][
        "record_count"
    ] == 0
    archive = observations["neso-regional-archive-2026-10-11-prior-assessment"]
    assert archive["evidence_status"] == "externally_reported"
    assert archive["response"]["response_sha256"] is None
    assert manifest["capture_completed_at_utc"] == "2026-10-11T20:10:03.506733Z"

    sources = {
        (item["source_id"], item.get("source_variant")): item
        for item in report["sources"]
    }
    assert sources[(LIVE_NESO_SOURCE_API, "regional")][
        "regional_actual_evaluation"
    ]["eligible"] is False
    assert sources[(LIVE_NESO_SOURCE_API, "national")][
        "measurement_classification"
    ] == "estimated_actual_national"
    ukpn = sources[("ukpn-carbon-intensity", None)]
    assert ukpn["historical_coverage"]["record_count"] == 32603
    assert ukpn["historical_coverage"]["lpn_record_count"] == 0
    assert ukpn["historical_coverage"]["non_null_blended_intensity_count"] == 15837
    assert "per_record_publication_timestamp" in ukpn["missing_or_unknown_metadata"]
    assert ukpn["source_provenance"]["row_identity_field"] is None
    assert sources[(HISTORICAL_NESO_SOURCE_API, None)][
        "source_provenance"
    ]["resource_id"] == "c16b0e19-c02a-44a8-ba05-4db2c0545a2a"


def test_source_assessment_cli_json_is_machine_readable_and_stable():
    report = regional_actual_assessment_report()
    source_ids = [item["source_id"] for item in report["sources"]]

    assert source_ids == sorted(source_ids)
    assert len(source_ids) > len(set(source_ids))
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


def test_evidence_manifest_distinguishes_verified_reported_and_unavailable_evidence():
    manifest = regional_actual_assessment_report()["evidence_manifest"]

    assert {item["evidence_status"] for item in manifest["observations"]} == {
        "verified",
        "externally_reported",
        "unavailable",
    }
    for observation in manifest["observations"]:
        response = observation["response"]
        if observation["evidence_status"] == "verified":
            assert observation["request"]["url"].startswith("https://")
            assert observation["verified_at_utc"].endswith("Z")
            assert len(response["response_sha256"]) == 64
            assert response["body_retained"] is False
        else:
            assert observation["verified_at_utc"] is None
            assert response["response_sha256"] is None


def test_assessment_cli_prints_the_pinned_machine_readable_report(capsys):
    assert assess_sources_cli([]) == 0
    output = capsys.readouterr().out

    assert json.loads(output) == regional_actual_assessment_report()
    assert output == regional_actual_assessment_json() + "\n"


def test_candidate_contracts_preserve_existing_source_unit_distinctions():
    assert units_for_source_api(LIVE_NESO_SOURCE_API) == "gCO2e/kWh"
    assert units_for_source_api(HISTORICAL_NESO_SOURCE_API) == "gCO2/kWh"
    regional_contract = SOURCE_CONTRACTS[
        (LIVE_NESO_SOURCE_API, "regional_carbon_intensity")
    ]
    national_contract = SOURCE_CONTRACTS[
        (LIVE_NESO_SOURCE_API, "national_carbon_intensity")
    ]
    assert regional_contract.units == "gCO2e/kWh"
    assert regional_contract.measurement_classes == ("forecast",)
    assert national_contract.geography_codes == ("GB",)
    assert national_contract.target_variable == "national_carbon_intensity"
    assert SOURCE_CONTRACTS[
        (HISTORICAL_NESO_SOURCE_API, "regional_carbon_intensity")
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
    assert report.record_decisions[0].publication_provenance_verified is True
    assert report.record_decisions[0].available_at_forecast_origin is None
    assert report.missing_intervals == ()
    assert report.target_window_complete is True
    assert report.as_of_feature_availability_at_origin is None


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


def test_target_can_be_valid_without_publication_time_but_availability_is_unknown():
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
    assert decision.publication_provenance_verified is False
    assert decision.available_at_forecast_origin is None
    assert report.as_of_feature_availability_at_origin is None
    assert decision.readiness_reasons == ("publication_time_unverified",)


def test_supplied_publication_timestamp_must_be_valid_even_if_source_time_is_unverified():
    contract = _contract(publication_time_verified=False)
    candidate = _candidate(
        published_at=datetime(2026, 10, 1, 12, 35, tzinfo=UTC).replace(tzinfo=None)
    )

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
        source_id=LIVE_NESO_SOURCE_API,
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


def test_existing_neso_parser_source_id_resolves_to_regional_forecast_contract():
    source_url = (
        "https://api.carbonintensity.org.uk/regional/regionid/13"
    )
    reading = parse_regional_response(
        {
            "data": [
                {
                    "regionid": 13,
                    "shortname": "London",
                    "data": [
                        {
                            "from": START.isoformat().replace("+00:00", "Z"),
                            "to": (START + timedelta(minutes=30))
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "intensity": {
                                "forecast": 42,
                                "actual": None,
                                "index": "moderate",
                            },
                        }
                    ],
                }
            ]
        },
        region_id=13,
        now=START,
        source_url=source_url,
    )
    candidate = CandidateTarget(
        source_id=reading.source_api,
        target_variable="regional_carbon_intensity",
        measurement_class=reading.measurement_type,
        geography_code="NESO-13",
        region_id=reading.region_id,
        region_name=reading.region_name,
        interval_start=reading.observed_at,
        interval_end=reading.valid_until,
        value=reading.intensity_gco2_per_kwh,
        units=reading.units,
        source_record_id=f"{reading.region_id}:{reading.observed_at.isoformat()}",
        source_url=reading.source_url,
        retrieved_at=reading.retrieved_at,
    )

    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
    )

    decision = report.record_decisions[0]
    assert reading.source_api == LIVE_NESO_SOURCE_API
    assert "unsupported_source" not in decision.rejection_reasons
    assert "forecast_not_actual" in decision.rejection_reasons
    assert "regional_actual_not_evidenced" in decision.rejection_reasons
    assert report.accepted_records == ()


def test_as_of_availability_requires_an_explicit_forecast_origin():
    contract = _contract()
    candidate = _candidate()

    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
    )

    decision = report.record_decisions[0]
    assert decision.publication_provenance_verified is True
    assert decision.available_at_forecast_origin is None
    assert report.as_of_feature_availability_at_origin is None


def test_value_published_after_forecast_origin_is_unavailable_at_that_origin():
    contract = _contract()
    candidate = _candidate(
        published_at=START + timedelta(minutes=45),
        retrieved_at=START + timedelta(minutes=50),
    )

    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
        forecast_origin=START + timedelta(minutes=40),
    )

    decision = report.record_decisions[0]
    assert decision.publication_provenance_verified is True
    assert decision.available_at_forecast_origin is False
    assert report.as_of_feature_availability_at_origin is False
    assert report.target_window_complete is True
    assert not hasattr(report, "actual_target_evaluation_ready")


def test_late_verified_publication_is_unavailable_even_without_retrieval_time():
    contract = _contract()
    candidate = _candidate(
        published_at=START + timedelta(minutes=45),
        retrieved_at=None,
    )

    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
        forecast_origin=START + timedelta(minutes=40),
    )

    assert report.record_decisions[0].publication_provenance_verified is True
    assert report.record_decisions[0].available_at_forecast_origin is False


def test_retrieval_after_forecast_origin_prevents_local_as_of_availability():
    contract = _contract()
    candidate = _candidate(
        published_at=START + timedelta(minutes=35),
        retrieved_at=START + timedelta(minutes=50),
    )

    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
        forecast_origin=START + timedelta(minutes=45),
    )

    assert report.record_decisions[0].available_at_forecast_origin is False
    assert report.as_of_feature_availability_at_origin is False


def test_value_is_available_at_origin_only_after_interval_publication_and_retrieval():
    contract = _contract()
    candidate = _candidate()

    report = validate_candidate_targets(
        [candidate],
        requested_start=START,
        requested_end=START + timedelta(minutes=30),
        source_contracts={contract.source_id: contract},
        forecast_origin=START + timedelta(minutes=45),
    )

    assert report.record_decisions[0].available_at_forecast_origin is True
    assert report.as_of_feature_availability_at_origin is True


def test_forecast_origin_must_be_timezone_aware():
    with pytest.raises(ValueError, match="forecast_origin must include a timezone"):
        validate_candidate_targets(
            [_candidate()],
            requested_start=START,
            requested_end=START + timedelta(minutes=30),
            source_contracts={_contract().source_id: _contract()},
            forecast_origin=datetime(2026, 10, 1, 13, 0, tzinfo=UTC).replace(
                tzinfo=None
            ),
        )


def test_national_actual_and_ukpn_estimate_are_rejected_for_london_target_scope():
    national = _candidate(
        source_id=LIVE_NESO_SOURCE_API,
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

    assert "target_variable_mismatch" in decisions[LIVE_NESO_SOURCE_API].rejection_reasons
    assert "geographic_scope_mismatch_national_only" in decisions[
        LIVE_NESO_SOURCE_API
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
    candidate = _candidate(
        interval_start=datetime(2026, 10, 1, 12, 0, tzinfo=UTC).replace(tzinfo=None)
    )
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
    assert report.target_window_complete is False
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
    assert report.target_window_complete is True
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
