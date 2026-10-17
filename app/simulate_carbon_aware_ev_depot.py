"""Run a bounded, offline carbon-aware EV depot simulation from JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app.schemas.ev_depot import CarbonAwareDepotScenario
from app.services.carbon_aware_scheduler import schedule_carbon_aware

_MAX_SCENARIO_FILE_BYTES = 256 * 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path, help="path to a bounded synthetic scenario JSON file")
    args = parser.parse_args(argv)

    try:
        with args.scenario.open("rb") as scenario_file:
            raw_scenario = scenario_file.read(_MAX_SCENARIO_FILE_BYTES + 1)
    except OSError as exc:
        parser.error(f"cannot read scenario file: {exc}")
    if len(raw_scenario) > _MAX_SCENARIO_FILE_BYTES:
        parser.error(f"scenario JSON exceeds the {_MAX_SCENARIO_FILE_BYTES}-byte limit")

    try:
        scenario = CarbonAwareDepotScenario.model_validate_json(raw_scenario)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
        parser.error(f"invalid scenario: {details}")

    result = schedule_carbon_aware(scenario)
    print(
        json.dumps(
            result.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
