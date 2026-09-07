from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from dnp3_master import (
    AnalogOutputCommand, ClientStateError, CrobCommand, Dnp3MasterClient,
    EmsTestPlanError, HostCommandError, HostProtocolError, HostTimeoutError,
    LabSafetyConfig, TcpConnectionConfig, load_ems_test_plan, load_point_table,
)
from dnp3_master.local_outstation import LocalTestOutstation
from test_client import fake_config
from test_pics_gating import write_profile


ROOT = Path(__file__).resolve().parents[2]
POINTS = ROOT / "config/points.example.csv"
PLAN = ROOT / "config/ems_test_plan.simulator.example.json"
TC_SIMULATOR_CONTROLS_LOCAL_001 = "TC_SIMULATOR_CONTROLS_LOCAL_001"


def test_simulator_model_and_default_lab_policy() -> None:
    assert "safety" not in TcpConnectionConfig(host="127.0.0.1").to_params()
    config = TcpConnectionConfig(host="127.0.0.1", simulator=True)
    assert config.to_params()["safety"] == {"environment": "SIMULATOR"}
    with pytest.raises(ValueError, match="mutually exclusive"):
        replace(config, safety=LabSafetyConfig("synthetic-operator", "synthetic-dut"))
    for invalid in (1, "true", None):
        with pytest.raises(ValueError, match="simulator must be boolean"):
            replace(config, simulator=invalid)


def test_simulator_controls_need_no_incident_store_and_allow_repetition() -> None:
    with Dnp3MasterClient(fake_config("tcp_api")) as client:
        connection = client.connect(TcpConnectionConfig(host="127.0.0.1", simulator=True))
        assert connection["safety"]["environment"] == "SIMULATOR"
        assert "safety_token" not in connection["safety"]
        assert client.simulator_mode and client.state_change_authorized
        for _ in range(3):
            assert client.direct_operate([CrobCommand(0, "latch_on")]).all_success
            assert client.direct_operate([
                AnalogOutputCommand(0, 1.25, "analog_output_float32"),
            ]).all_success
        client.disconnect()
        assert not client.state_change_authorized
        client.connect(TcpConnectionConfig(host="127.0.0.1"))
        assert not client.simulator_mode
        with pytest.raises(ClientStateError, match="locked"):
            client.direct_operate([CrobCommand(0, "latch_off")])


@pytest.mark.parametrize("index, error_type", [
    (65535, HostCommandError), (65534, HostTimeoutError),
    (65533, HostCommandError), (65532, HostProtocolError), (65531, HostCommandError),
])
def test_simulator_failure_closes_host_without_locking_future_sessions(
    tmp_path: Path, index: int, error_type: type[Exception],
) -> None:
    incident_dir = tmp_path / "incidents"
    config = fake_config("tcp_api", safety_incident_directory=incident_dir)
    with Dnp3MasterClient(config) as client:
        client.connect(TcpConnectionConfig(host="127.0.0.1", simulator=True))
        with pytest.raises(error_type) as caught:
            client.direct_operate([CrobCommand(index, "latch_on")], request_timeout=0.2)
        assert caught.value.details["persistent_safety_lock"] is False
        assert caught.value.details["required_action"] == "START_NEW_SESSION"
        assert not client.is_running and not client.state_change_authorized
    assert not incident_dir.exists()
    with Dnp3MasterClient(config) as replacement:
        replacement.connect(TcpConnectionConfig(host="127.0.0.1", simulator=True))
        assert replacement.direct_operate([CrobCommand(0, "latch_off")]).all_success
    assert not incident_dir.exists()


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_simulator_interruption_keeps_original_exception_without_incident(
    monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException],
) -> None:
    with Dnp3MasterClient(fake_config("tcp_api")) as client:
        client.connect(TcpConnectionConfig(host="127.0.0.1", simulator=True))

        def interrupt(*args: object) -> None:
            raise interruption()

        monkeypatch.setattr(client, "_validate_command_correlation", interrupt)
        with pytest.raises(interruption) as caught:
            client.direct_operate([CrobCommand(0, "latch_on")])
        assert caught.value.details["persistent_safety_lock"] is False
        assert not client.is_running


@pytest.mark.parametrize("simulator, reported_mode, authorized", [
    (True, "LAB", True), (True, "SIMULATOR", False), (False, "SIMULATOR", True),
])
def test_connect_rejects_mismatched_simulator_handshake(
    monkeypatch: pytest.MonkeyPatch, simulator: bool, reported_mode: str, authorized: bool,
) -> None:
    with Dnp3MasterClient(fake_config("tcp_api")) as client:
        monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {
            "safety": {
                "environment": reported_mode, "state_change_authorized": authorized,
                "safety_token": "0123456789abcdef0123456789abcdef" if authorized else None,
            },
        })
        with pytest.raises(HostProtocolError, match="inconsistent"):
            client.connect(TcpConnectionConfig(host="127.0.0.1", simulator=simulator))
        assert not client.is_running and not client.state_change_authorized


def test_simulator_plan_roundtrip_and_optional_restore(tmp_path: Path) -> None:
    points = load_point_table(POINTS)
    plan = load_ems_test_plan(PLAN, points)
    assert plan.environment == "SIMULATOR"
    assert len(plan.enabled_control_scenarios) == 2
    assert plan.control_scenarios[0].precondition is None
    document = plan.to_mapping()
    scenario = document["control_scenarios"][0]
    scenario["restore_command"] = deepcopy(scenario["command"])
    scenario["restore_expectation"] = deepcopy(scenario["postcondition"])
    path = tmp_path / "sim.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert load_ems_test_plan(path, points).to_mapping() == document
    del scenario["restore_expectation"]
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(EmsTestPlanError, match="supplied together"):
        load_ems_test_plan(path, points)
    document = plan.to_mapping()
    del document["environment"]
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(EmsTestPlanError, match="missing"):
        load_ems_test_plan(path, points)


def _nested(pytester: pytest.Pytester, body: str) -> None:
    pytester.makeconftest('pytest_plugins = ("dnp3_master.pytest_plugin",)')
    pytester.makepyfile(body)


@pytest.mark.parametrize("source", ["ini", "env", "cli"])
def test_pytest_simulator_mode_needs_no_pics_approval_or_identity(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
    monkeypatch.delenv("DNP3_SIMULATOR", raising=False)
    _nested(pytester, '''
        import pytest
        @pytest.mark.dnp3_dut
        @pytest.mark.dnp3_state_changing
        @pytest.mark.dnp3_capability("APP.FC.05.DIRECT_OPERATE")
        def test_configuration(dnp3_connection_config):
            assert dnp3_connection_config.simulator is True
            assert dnp3_connection_config.safety is None
    ''')
    args = ["-q", "-p", "dnp3_master.pytest_plugin", "--dnp3-outstation-host=127.0.0.1"]
    if source == "ini":
        pytester.makeini("[pytest]\ndnp3_simulator = true")
    elif source == "env":
        monkeypatch.setenv("DNP3_SIMULATOR", "1")
    else:
        args += ["--dnp3-simulator"]
    result = pytester.runpytest(*args)
    result.assert_outcomes(passed=1)


def test_pytest_requires_mode_for_simulator_plan(pytester: pytest.Pytester) -> None:
    _nested(pytester, "def test_never_runs(): assert False")
    result = pytester.runpytest(
        "-q", "-p", "dnp3_master.pytest_plugin",
        "--dnp3-points-file", str(POINTS), "--dnp3-ems-plan", str(PLAN),
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*SIMULATOR plan requires*"])


def test_simulator_respects_explicit_unsupported_and_unimplemented_capabilities(
    pytester: pytest.Pytester,
) -> None:
    _nested(pytester, '''
        import pytest
        @pytest.mark.dnp3_dut
        @pytest.mark.dnp3_capability("APP.FC.05.DIRECT_OPERATE")
        def test_not_supported(): assert False
        @pytest.mark.dnp3_dut
        @pytest.mark.dnp3_capability("APP.FC.06.DIRECT_OPERATE_NR")
        def test_unimplemented(): assert False
    ''')
    profile = write_profile(pytester.path / "pics.json", {
        "APP.FC.05.DIRECT_OPERATE": "NOT_SUPPORTED",
        "APP.FC.06.DIRECT_OPERATE_NR": "SUPPORTED",
    })
    result = pytester.runpytest(
        "-q", "-rs", "-p", "dnp3_master.pytest_plugin", "--dnp3-simulator",
        "--dnp3-pics-file", str(profile),
    )
    result.assert_outcomes(skipped=2)
    result.stdout.fnmatch_lines(["*NOT_SUPPORTED*", "*framework capability is not ready*"])


@pytest.mark.parametrize("environment, cli, expected", [
    ("0", False, False), ("false", True, True), ("1", False, True),
])
def test_simulator_configuration_precedence(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch,
    environment: str, cli: bool, expected: bool,
) -> None:
    monkeypatch.setenv("DNP3_SIMULATOR", environment)
    _nested(pytester, f"def test_mode(pytestconfig): assert pytestconfig._dnp3_simulator is {expected}")
    pytester.makeini("[pytest]\ndnp3_simulator=true")
    result = pytester.runpytest(
        "-q", "-p", "dnp3_master.pytest_plugin", *(["--dnp3-simulator"] if cli else []),
    )
    result.assert_outcomes(passed=1)


def test_simulator_rejects_invalid_environment_flag(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DNP3_SIMULATOR", "maybe")
    _nested(pytester, "def test_never_runs(): assert False")
    result = pytester.runpytest("-q", "-p", "dnp3_master.pytest_plugin")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*DNP3_SIMULATOR must be a boolean*"])


def test_simulator_policy_stays_fixed_after_collection(pytester: pytest.Pytester) -> None:
    pytester.makeini("[pytest]\ndnp3_simulator=true")
    _nested(pytester, '''
        import pytest
        @pytest.fixture(autouse=True)
        def change_environment(monkeypatch):
            monkeypatch.setenv("DNP3_SIMULATOR", "false")
        def test_configuration(dnp3_connection_config):
            assert dnp3_connection_config.simulator is True
    ''')
    result = pytester.runpytest(
        "-q", "-p", "dnp3_master.pytest_plugin",
        "--dnp3-outstation-host=127.0.0.1",
    )
    result.assert_outcomes(passed=1)


def test_pytest_next_simulator_test_gets_new_host_after_failure(pytester: pytest.Pytester) -> None:
    _nested(pytester, '''
        from dnp3_master import CrobCommand
        def test_first(connected_master):
            connected_master.direct_operate([CrobCommand(65535, "latch_on")])
        def test_second(connected_master):
            assert connected_master.direct_operate([CrobCommand(0, "latch_on")]).all_success
    ''')
    pytester.makeconftest('''
        import pytest
        from dataclasses import replace
        pytest_plugins = ("dnp3_master.pytest_plugin",)
        @pytest.fixture(scope="session")
        def dnp3_host_config(dnp3_host_config):
            # The NDJSON-only fake intentionally reports a synthetic matrix hash.
            return replace(dnp3_host_config, expected_capability_matrix_sha256="0" * 64)
    ''')
    result = pytester.runpytest(
        "-q", "-p", "dnp3_master.pytest_plugin",
        "--dnp3-simulator", "--dnp3-outstation-host=127.0.0.1",
        f"--dnp3-host-exe={sys.executable}",
        f"--dnp3-host-arg={Path(__file__).with_name('fake_host.py')}",
        "--dnp3-host-arg=--mode", "--dnp3-host-arg=tcp_api",
    )
    # The failed exchange is visible both at the call and forced cleanup boundary.
    result.assert_outcomes(passed=1, failed=1, errors=1)
    assert not (pytester.path / "evidence/local/safety-incidents").exists()


def test_simulator_schema_preserves_lab_requirements() -> None:
    schema = json.loads((ROOT / "schemas/ems-test-plan.schema.json").read_text(encoding="utf-8"))
    conditional = schema["allOf"][0]
    assert conditional["if"]["required"] == ["environment"]
    assert conditional["if"]["properties"]["environment"]["const"] == "SIMULATOR"
    assert set(conditional["else"]["properties"]["control_scenarios"]["items"]["required"]) == {
        "authorization_reference", "precondition", "restore_command", "restore_expectation",
    }
    assert "postcondition" in schema["$defs"]["controlScenario"]["required"]
    request = json.loads((ROOT / "schemas/request.schema.json").read_text(encoding="utf-8"))
    sim, lab = request["$defs"]["connectionSafety"]["oneOf"]
    assert sim["required"] == ["environment"] and sim["additionalProperties"] is False
    assert set(lab["required"]) == {"environment", "allow_state_change", "operator_id", "dut_id"}


def test_native_simulator_templates_batch_repeat_and_evidence(pytester: pytest.Pytester) -> None:
    assert TC_SIMULATOR_CONTROLS_LOCAL_001
    host = os.environ.get("DNP3_MASTER_HOST_EXE")
    outstation = os.environ.get("DNP3_TEST_OUTSTATION_EXE")
    if not host or not outstation:
        pytest.skip("requires built native host and bundled loopback outstation")
    cases = pytester.path / "cases"
    cases.mkdir()
    for name in ("__init__.py", "conftest.py", "_scenario_helpers.py", "test_control_scenarios.py"):
        shutil.copyfile(ROOT / "examples/pytest_ems" / name, cases / name)
    # Parametrize the same scenario twice, in a single pytest process.
    with (cases / "conftest.py").open("a", encoding="utf-8") as stream:
        stream.write('\ndef pytest_collection_modifyitems(items):\n    items.extend(list(items))\n')
    evidence = pytester.path / "evidence"
    with LocalTestOutstation(Path(outstation)) as simulator:
        result = pytester.runpytest(
            "-q", str(cases), "--dnp3-simulator", "--dnp3-host-exe", host,
            "--dnp3-outstation-host=127.0.0.1", f"--dnp3-outstation-port={simulator.port}",
            "--dnp3-points-file", str(POINTS), "--dnp3-ems-plan", str(PLAN),
            "--dnp3-evidence-dir", str(evidence),
        )
    result.assert_outcomes(passed=4)
    manifest = json.loads(next(evidence.glob("*/manifest.json")).read_text(encoding="utf-8"))
    assert manifest["runner"]["dnp3_environment"] == "SIMULATOR"
    assert not (evidence / "local/safety-incidents").exists()
