from __future__ import annotations

import json
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
POINTS = REPOSITORY_ROOT / "config" / "points.example.csv"
PLAN = REPOSITORY_ROOT / "config" / "ems_test_plan.example.json"


def configure_nested_test(pytester: pytest.Pytester, body: str) -> None:
    pytester.makeconftest('pytest_plugins = ("dnp3_master.pytest_plugin",)')
    pytester.makepyfile(body)


def test_plugin_loads_plan_and_hashes_it_as_private_input(
    pytester: pytest.Pytester,
) -> None:
    configure_nested_test(
        pytester,
        """
        def test_plan_fixture(dnp3_ems_test_plan):
            assert dnp3_ems_test_plan is not None
            assert len(dnp3_ems_test_plan.enabled_poll_scenarios) == 4
        """,
    )
    evidence = pytester.path / "evidence"

    result = pytester.runpytest(
        "-q",
        "-p",
        "dnp3_master.pytest_plugin",
        "--dnp3-points-file",
        str(POINTS),
        "--dnp3-ems-plan",
        str(PLAN),
        "--dnp3-evidence-dir",
        str(evidence),
    )

    result.assert_outcomes(passed=1)
    run_directory = next(path for path in evidence.iterdir() if path.is_dir())
    manifest = json.loads(
        (run_directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["inputs"]["ems_test_plan"]["present"] is True
    assert manifest["inputs"]["ems_test_plan"]["file_name"] == PLAN.name
    assert "sha256" in manifest["inputs"]["ems_test_plan"]


def test_plan_requires_point_table_before_collection(
    pytester: pytest.Pytester,
) -> None:
    configure_nested_test(pytester, "def test_never_runs(): assert False")

    result = pytester.runpytest(
        "-q",
        "-p",
        "dnp3_master.pytest_plugin",
        "--dnp3-ems-plan",
        str(PLAN),
    )

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*EMS test plan requires*points-file*"])


def test_disabled_control_cannot_be_selected(
    pytester: pytest.Pytester,
) -> None:
    configure_nested_test(pytester, "def test_never_runs(): assert False")

    result = pytester.runpytest(
        "-q",
        "-p",
        "dnp3_master.pytest_plugin",
        "--dnp3-points-file",
        str(POINTS),
        "--dnp3-ems-plan",
        str(PLAN),
        "--dnp3-control-scenario",
        "binary-output-latch-cycle",
    )

    assert result.ret != 0
    result.stderr.fnmatch_lines(["*control scenario*is disabled*"])


def test_control_selection_has_no_environment_fallback(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DNP3_CONTROL_SCENARIO", "binary-output-latch-cycle")
    configure_nested_test(
        pytester,
        """
        def test_no_selection(pytestconfig):
            assert pytestconfig.getoption("--dnp3-control-scenario") is None
            assert pytestconfig._dnp3_selected_control_scenario is None
        """,
    )

    result = pytester.runpytest(
        "-q",
        "-p",
        "dnp3_master.pytest_plugin",
        "--dnp3-points-file",
        str(POINTS),
        "--dnp3-ems-plan",
        str(PLAN),
    )

    result.assert_outcomes(passed=1)


def test_enabled_control_requires_an_exact_command_line_selection(
    pytester: pytest.Pytester,
) -> None:
    document = json.loads(PLAN.read_text(encoding="utf-8"))
    document["control_scenarios"][0]["enabled"] = True
    document["control_scenarios"][0]["authorization_reference"] = (
        "APPROVED-CHANGE-123"
    )
    enabled_plan = pytester.path / "enabled-plan.json"
    enabled_plan.write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )
    configure_nested_test(
        pytester,
        """
        def test_selected(pytestconfig):
            selected = pytestconfig._dnp3_selected_control_scenario
            assert selected.scenario_id == "binary-output-latch-cycle"
            assert selected.enabled is True
        """,
    )

    result = pytester.runpytest(
        "-q",
        "-p",
        "dnp3_master.pytest_plugin",
        "--dnp3-points-file",
        str(POINTS),
        "--dnp3-ems-plan",
        str(enabled_plan),
        "--dnp3-control-scenario",
        "binary-output-latch-cycle",
    )

    result.assert_outcomes(passed=1)
