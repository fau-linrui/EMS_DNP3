from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from dnp3_master import Dnp3MasterClient
from dnp3_master.local_outstation import LocalTestOutstation
from dnp3_master import simulator_suite as suite

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/pytest_simulator"
TC_SIMULATOR_STARTER_LOCAL_001 = "TC_SIMULATOR_STARTER_LOCAL_001"


def example() -> dict:
    return json.loads((EXAMPLE / "settings.example.json").read_text(encoding="utf-8"))


def write_settings(tmp_path: Path, data: dict | None = None) -> Path:
    path = tmp_path / "settings.local.json"
    path.write_text(json.dumps(example() if data is None else data), encoding="utf-8")
    return path


def test_example_and_activation_model(tmp_path):
    settings = suite.load_simulator_settings(write_settings(tmp_path))
    assert settings.connection.simulator and settings.connection.safety is None
    assert len(settings.points) == 4 and len(settings.controls) == 5 and len(settings.events) == 2
    assert settings.class_counts == ((1, 0), (2, 0), (3, 0))
    on, off = (case.command() for case in settings.controls[:2])
    assert (on.operation, off.operation) == ("latch_on", "latch_off")
    assert on.count == 1 and on.on_time_ms == on.off_time_ms == 0
    assert settings.controls[2].command().command_type == "analog_output_float32"


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(unknown=1),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(environment="LAB"),
    lambda d: d["connection"].update(simulator=False),
    lambda d: d["connection"].update(port=True),
    lambda d: d["connection"].update(master_address=1024),
    lambda d: d.update(points=[]),
    lambda d: d["points"].append(deepcopy(d["points"][0])),
    lambda d: d["points"][0].update(kind="COUNTER"),
    lambda d: d["points"][0].update(index=65536),
    lambda d: d["points"][0].update(required_flags=256),
    lambda d: d["points"][0].update(expected=1),
    lambda d: d["controls"][0].update(value=1),
    lambda d: d["controls"][2].update(value=1e40),
    lambda d: d["controls"][0].update(feedback_point="missing"),
    lambda d: d["controls"][0].update(feedback_point="AI_0"),
    lambda d: d["controls"].append(deepcopy(d["controls"][0])),
    lambda d: d["events"][0].update(point="BO_0"),
    lambda d: d["events"][0].update(require_timestamp=1),
    lambda d: d["events"][0].update(require_change="false"),
    lambda d: d["class_counts"].append({"class": 1, "count": 0}),
    lambda d: d["class_counts"][0].update(count=65537),
    lambda d: d.update(event_timeout=3601),
    lambda d: d.update(task_timeout=0),
    lambda d: d.update(feedback_timeout=float("inf")),
    lambda d: d.update(runtime_root=""),
    lambda d: d.update(max_measurements=0),
    lambda d: d.update(events=[deepcopy(d["events"][0])] * 129),
])
def test_strict_settings_reject_before_network(tmp_path, mutate):
    data = example()
    mutate(data)
    with pytest.raises(ValueError):
        suite.load_simulator_settings(write_settings(tmp_path, data))


@pytest.mark.parametrize("content", ['{"a":1,"a":2}', '{"x":NaN}', '[1]', ' ' * (1024 * 1024 + 1)], ids=["duplicate", "nan", "root", "oversized"])
def test_invalid_or_oversized_json(tmp_path, content):
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        suite.load_simulator_settings(path)


def make_runtime(root):
    (root / "bin").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "bin/dnp3-master-host.exe").touch()  # Metadata test only; never launched.
    shutil.copyfile(ROOT / "config/capability_matrix.csv", root / "config/capability_matrix.csv")
    import hashlib
    info = dict(host_version="0.6.1", opendnp3_version="3.1.2", target_architecture="x64", protocol_schema_version=1,
                capability_matrix_sha256=hashlib.sha256((root / "config/capability_matrix.csv").read_bytes()).hexdigest())
    (root / "bin/build-info.json").write_text(json.dumps(info), encoding="utf-8")


def test_runtime_nearest_root_and_explicit_relative_root(tmp_path):
    package = tmp_path / "runtime"
    make_runtime(package)
    test_dir = package / "examples/pytest_simulator"
    test_dir.mkdir(parents=True)
    settings = suite.load_simulator_settings(write_settings(test_dir))
    assert suite.find_simulator_runtime(settings).root == package
    data = example()
    data["runtime_root"] = "runtime"
    assert suite.find_simulator_runtime(suite.load_simulator_settings(write_settings(tmp_path, data))).root == package
    (package / "config/capability_matrix.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(suite.SimulatorCheckError, match="INSTALLATION.*mismatch"):
        suite.find_simulator_runtime(settings)


def test_missing_host_is_actionable(tmp_path):
    settings = suite.load_simulator_settings(write_settings(tmp_path))
    with pytest.raises(suite.SimulatorCheckError, match="INSTALLATION.*runtime_root"):
        suite.find_simulator_runtime(settings)


@pytest.mark.parametrize("field,value", [("host_version", "0.0.0"), ("target_architecture", "x86"), ("opendnp3_version", "0.0.0")])
def test_runtime_metadata_rejects_mixed_install(tmp_path, field, value):
    make_runtime(tmp_path / "runtime")
    info_path = tmp_path / "runtime/bin/build-info.json"
    info = json.loads(info_path.read_text())
    info[field] = value
    info_path.write_text(json.dumps(info))
    data = example()
    data["runtime_root"] = "runtime"
    with pytest.raises(suite.SimulatorCheckError, match="mismatch"):
        suite.find_simulator_runtime(suite.load_simulator_settings(write_settings(tmp_path, data)))


def result(**overrides):
    values = dict(task_status="SUCCESS", task_started=True, iin={"bits": [], "observation_window_dropped": 0},
                  return_mode="detail", measurements=(), summary={"received_total": 0})
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("overrides", [
    {"task_status": "FAILURE"}, {"task_started": False},
    {"iin": {"bits": ["IIN2.1.OBJECT_UNKNOWN"]}},
    {"iin": {"bits": [], "observation_window_dropped": 1}},
    {"return_mode": "summary"}, {"summary": {"received_total": 1}},
])
def test_read_result_cannot_false_pass(overrides):
    with pytest.raises(suite.SimulatorCheckError):
        suite.check_read(result(**overrides))


def test_exact_zero_class_rejects_nonempty_response(tmp_path):
    sys.path.insert(0, str(ROOT))
    from examples.pytest_simulator.test_basic import test_class_event_exact_count as run
    settings = suite.load_simulator_settings(write_settings(tmp_path))
    client = SimpleNamespace(class_poll=lambda *a, **k: result(measurements=(object(),), summary={"received_total": 1}))
    with pytest.raises(AssertionError, match="CLASS_COUNT"):
        run(client, settings, (1, 0))


@pytest.mark.parametrize("failed_stage", ["HOST_START", "TCP_CONNECT", "DNP3_READ"])
def test_setup_diagnoses_layers_and_closes_once(tmp_path, monkeypatch, failed_stage):
    settings = suite.load_simulator_settings(write_settings(tmp_path))
    closed = []
    def step(stage):
        if stage == failed_stage:
            raise RuntimeError("synthetic failure")
    client = SimpleNamespace(start=lambda: step("HOST_START"), connect=lambda *a, **k: step("TCP_CONNECT"),
                             close=lambda: closed.append(1) or SimpleNamespace(cleanup_error="secondary cleanup"))
    monkeypatch.setattr(suite, "Dnp3MasterClient", lambda host: client)
    monkeypatch.setattr(suite, "read_simulator_point", lambda *a, **k: step("DNP3_READ"))
    with pytest.raises(suite.SimulatorCheckError) as caught:
        with suite.simulator_session(settings, None):
            pytest.fail("setup must not yield")
    assert caught.value.stage == failed_stage and closed == [1]
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_feedback_timeout_does_not_resend_or_restore(tmp_path, monkeypatch):
    settings = replace(suite.load_simulator_settings(write_settings(tmp_path)), feedback_timeout=0.1)
    sent = []
    client = SimpleNamespace(simulator_mode=True, direct_operate=lambda commands, **k: sent.extend(commands) or SimpleNamespace(
        all_success=True, task_status="SUCCESS", execution_uncertain=False, point_results=[SimpleNamespace(index=0)]))
    monkeypatch.setattr(suite, "read_simulator_point", lambda *a, **k: SimpleNamespace(value=False))
    with pytest.raises(suite.SimulatorCheckError, match="CONTROL_FEEDBACK"):
        suite.run_simulator_control(client, settings, settings.controls[0])
    assert len(sent) == 1


@pytest.mark.parametrize("fault,stage", [
    ("dropped", "EVENT_QUEUE"), ("session", "EVENT_SESSION"),
    ("timestamp", "EVENT_TIMESTAMP"), ("same_value", "EVENT_TIMEOUT"),
    ("wrong_index", "EVENT_TIMEOUT"),
])
def test_event_rejects_invalid_or_unchanged_observation(tmp_path, monkeypatch, fault, stage):
    settings = replace(suite.load_simulator_settings(write_settings(tmp_path)), event_timeout=0.1)
    event = settings.events[0]
    measurement = SimpleNamespace(kind="binary_input", group=2, variation=2, index=0, value=True,
        is_event=True, source="unsolicited", session_id=1, dnp3_timestamp_ms=1700000000000)
    if fault == "session":
        measurement.session_id = 2
    if fault == "timestamp":
        measurement.dnp3_timestamp_ms = None
    if fault == "wrong_index":
        measurement.index = 9
    disabled = []
    calls = []
    def wait(**kwargs):
        calls.append(1)
        assert len(calls) <= 2
        # Each wait consumes a bounded observation window, like the real host.
        if fault in ("same_value", "wrong_index"):
            import time
            time.sleep(0.11)
        return SimpleNamespace(enabled=True, classes=(1,), session_id=1, measurements=[measurement],
                               summary={"dropped_total": 1 if fault == "dropped" else 0})
    client = SimpleNamespace(enable_unsolicited=lambda *a, **k: SimpleNamespace(task_status="SUCCESS", task_started=True),
        disable_unsolicited=lambda *a, **k: disabled.append(1) or SimpleNamespace(task_status="SUCCESS"), wait_unsolicited=wait)
    monkeypatch.setattr(suite, "read_simulator_point", lambda *a: SimpleNamespace(value=fault == "same_value"))
    with pytest.raises(suite.SimulatorCheckError) as caught:
        suite.observe_simulator_event(client, settings, event)
    assert caught.value.stage == stage and disabled == [1]


def test_plugin_resolves_ini_relative_to_ini_and_records_input(pytester, monkeypatch):
    for name in list(os.environ):
        if name.startswith("DNP3_"):
            monkeypatch.delenv(name)
    make_runtime(pytester.path / "runtime")
    data = example()
    data["runtime_root"] = "runtime"
    write_settings(pytester.path, data)
    pytester.makeini("[pytest]\ndnp3_simulator=true\ndnp3_simulator_settings=settings.local.json")
    pytester.makepyfile('''
        def test_config(dnp3_simulator_settings, dnp3_connection_config, dnp3_host_config):
            assert dnp3_connection_config.simulator
            assert dnp3_connection_config.host == "192.0.2.10"
            assert dnp3_host_config.executable.name == "dnp3-master-host.exe"
            assert len(dnp3_simulator_settings.controls) == 5
    ''')
    monkeypatch.setenv("DNP3_OUTSTATION_HOST", "ignored.example.invalid")
    monkeypatch.setenv("DNP3_MASTER_HOST_EXE", "ignored.exe")
    report = pytester.runpytest("-q", "-p", "dnp3_master.pytest_plugin", "--dnp3-evidence-dir=evidence")
    report.assert_outcomes(passed=1)
    manifest_path = next((pytester.path / "evidence").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["inputs"]["simulator_settings"]["present"]
    assert "192.0.2.10" not in manifest_path.read_text(encoding="utf-8")


def test_settings_never_silently_enable_lab_controls(pytester):
    pytester.makepyfile("def test_never(): assert False")
    output = pytester.runpytest("-p", "dnp3_master.pytest_plugin", "--dnp3-simulator-settings=missing.json")
    assert output.ret == pytest.ExitCode.USAGE_ERROR
    output.stderr.fnmatch_lines(["*settings require*dnp3-simulator*"])


def test_copied_starter_full_native_loopback(tmp_path):
    """Exercise the actual copied pytest directory; only a local test driver generates signals."""
    assert TC_SIMULATOR_STARTER_LOCAL_001
    host = os.environ.get("DNP3_MASTER_HOST_EXE")
    outstation_exe = os.environ.get("DNP3_TEST_OUTSTATION_EXE")
    assert host and outstation_exe, "run scripts/test.ps1 or supply the two local native executables"
    copied = tmp_path / "consumer"
    copied.mkdir()
    for name in ("conftest.py", "pytest.ini", "test_basic.py", "settings.example.json"):
        shutil.copyfile(EXAMPLE / name, copied / name)
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "config").mkdir()
    shutil.copyfile(host, runtime / "bin/dnp3-master-host.exe")
    shutil.copyfile(Path(host).parent / "build-info.json", runtime / "bin/build-info.json")
    shutil.copyfile(ROOT / "config/capability_matrix.csv", runtime / "config/capability_matrix.csv")
    with LocalTestOutstation(Path(outstation_exe)) as outstation:
        data = example()
        data["runtime_root"] = "../runtime"
        data["connection"].update(host="127.0.0.1", port=outstation.port)
        data["event_timeout"] = 5
        # Explicitly establish a baseline; this driver is not shipped in the starter.
        outstation.update_binary_input(False, event_mode="suppress")
        outstation.update_analog_input(0.0, event_mode="suppress")
        write_settings(copied, data)
        env = {k: v for k, v in os.environ.items() if not k.startswith("DNP3_")}
        process = subprocess.Popen([sys.executable, "-m", "pytest", "-c", str(copied / "pytest.ini"), str(copied), "-q", "-s"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        lines = []
        errors = []
        def consume():
            try:
                for line in process.stdout:
                    if sum(map(len, lines)) > 1024 * 1024:
                        raise AssertionError("nested test output exceeded 1 MiB")
                    lines.append(line)
                    if "DNP3 EVENT READY: BI_change" in line:
                        outstation.update_binary_input(True, timestamp_ms=1700000000101, event_mode="force")
                    if "DNP3 EVENT READY: AI_change" in line:
                        outstation.update_analog_input(42.0, timestamp_ms=1700000000102, event_mode="force")
            except BaseException as error:
                errors.append(error)
        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        try:
            process.wait(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=5)
            process.stdout.close()
        assert not reader.is_alive() and not errors, errors
        assert process.returncode == 0, "".join(lines)
        assert "16 passed" in "".join(lines)
