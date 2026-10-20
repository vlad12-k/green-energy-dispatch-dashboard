from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time

from app.services.carbon_aware_scheduler import schedule_carbon_aware
from app.services.ev_depot_evaluation import (
    generate_maximum_supported_scenario,
    profile_segment_count,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure local execution time for the maximum supported EV depot scenario."
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Number of timed evaluations (1-100; default: 3).",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.iterations <= 100:
        parser.error("--iterations must be between 1 and 100")

    scenario = generate_maximum_supported_scenario()
    samples: list[float] = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        schedule_carbon_aware(scenario)
        samples.append(time.perf_counter() - started)

    report = {
        "measurement": "local_non_deterministic_timing_probe",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "iterations": args.iterations,
        "scenario_id": scenario.scenario_id,
        "interval_count": scenario.intervals,
        "vehicle_count": len(scenario.vehicles),
        "temporal_segment_count": profile_segment_count(scenario),
        "elapsed_seconds": {
            "minimum": min(samples),
            "median": statistics.median(samples),
            "maximum": max(samples),
        },
        "note": "Timing is local and workload-dependent; it is not deterministic result data.",
    }
    json.dump(report, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
