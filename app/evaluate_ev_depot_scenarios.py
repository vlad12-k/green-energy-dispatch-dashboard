from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.services.ev_depot_evaluation import evaluate_suite, load_evaluation_suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the existing EV depot strategies against a bounded, "
            "synthetic scenario suite."
        )
    )
    parser.add_argument(
        "--suite",
        type=Path,
        help="Optional local scenario suite JSON; defaults to the bundled synthetic suite.",
    )
    args = parser.parse_args(argv)
    try:
        suite = load_evaluation_suite(args.suite)
        result = evaluate_suite(suite)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        parser.error(f"invalid evaluation suite: {exc}")
    json.dump(
        result.model_dump(mode="json"),
        sys.stdout,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
