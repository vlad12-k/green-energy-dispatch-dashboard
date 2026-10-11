import json

import pytest
import structlog

from app import collect_neso
from app.core.logging import setup_logging


def test_run_command_requires_explicit_opt_in_before_database_access(monkeypatch) -> None:
    monkeypatch.setattr(collect_neso.settings, "collector_enabled", False)
    monkeypatch.setattr(
        collect_neso,
        "_database_engine",
        lambda: pytest.fail("disabled collector must not open a database connection"),
    )

    with pytest.raises(SystemExit, match="COLLECTOR_ENABLED=true"):
        collect_neso.main(["run"])


def test_collector_logging_emits_machine_readable_json(capsys) -> None:
    setup_logging(json_output=True)
    try:
        structlog.get_logger("collector-test").info(
            "collector_cycle_complete",
            run_id=1,
            outcome="succeeded",
        )
        record = json.loads(capsys.readouterr().out)

        assert record["event"] == "collector_cycle_complete"
        assert record["run_id"] == 1
        assert record["outcome"] == "succeeded"
    finally:
        setup_logging()
