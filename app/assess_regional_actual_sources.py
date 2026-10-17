"""Print the pinned regional actual-source assessment as deterministic JSON."""

from __future__ import annotations

import argparse

from app.services.regional_actual_assessment import regional_actual_assessment_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(regional_actual_assessment_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
