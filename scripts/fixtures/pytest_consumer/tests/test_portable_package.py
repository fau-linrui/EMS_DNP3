from __future__ import annotations

import json
import os
from pathlib import Path

import dnp3_master


def test_import_comes_from_isolated_install_target() -> None:
    installed_root = Path(os.environ["DNP3_EXPECTED_INSTALLED_ROOT"]).resolve()
    imported = Path(dnp3_master.__file__).resolve()

    assert imported.is_relative_to(installed_root), (
        f"dnp3_master was imported from {imported}, not the isolated install "
        f"target {installed_root}"
    )


def test_python_host_and_manifest_versions_match() -> None:
    package_root = Path(os.environ["DNP3_EXPECTED_PACKAGE_ROOT"]).resolve()
    expected_version = os.environ["DNP3_EXPECTED_PACKAGE_VERSION"]
    build_info = json.loads(
        (package_root / "bin" / "build-info.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (package_root / "package-manifest.json").read_text(encoding="utf-8")
    )

    assert dnp3_master.__version__ == expected_version
    assert build_info["host_version"] == expected_version
    assert manifest["package_version"] == expected_version
    assert (package_root / "bin" / "dnp3-master-host.exe").is_file()


def test_plugin_options_and_safe_empty_defaults_are_available(
    pytestconfig,
    dnp3_pics,
    dnp3_point_table,
    dnp3_ems_test_plan,
    dnp3_performance_profile,
    dnp3_local_event_profile,
) -> None:
    assert pytestconfig.getoption("--dnp3-host-exe") is None
    assert pytestconfig.getoption("--dnp3-allow-state-changing") is False
    assert pytestconfig.getoption("--dnp3-simulator") is False
    assert pytestconfig.getini("dnp3_simulator") is False
    assert dnp3_pics == {}
    assert dnp3_point_table is None
    assert dnp3_ems_test_plan is None
    assert dnp3_performance_profile is None
    assert dnp3_local_event_profile is None


def test_packaged_simulator_mode_without_lab_identity_or_incident_store() -> None:
    from dnp3_master.local_outstation import LocalTestOutstation

    package_root = Path(os.environ["DNP3_EXPECTED_PACKAGE_ROOT"]).resolve()
    points = dnp3_master.load_point_table(package_root / "config/points.example.csv")
    plan = dnp3_master.load_ems_test_plan(
        package_root / "config/ems_test_plan.simulator.example.json", points,
    )
    assert plan.environment == "SIMULATOR"
    with LocalTestOutstation(package_root / "tools/dnp3-local-test-outstation.exe") as simulator:
        with dnp3_master.Dnp3MasterClient(dnp3_master.HostProcessConfig(
            package_root / "bin/dnp3-master-host.exe",
        )) as client:
            client.connect(dnp3_master.TcpConnectionConfig(
                host="127.0.0.1", port=simulator.port, simulator=True,
            ))
            assert client.simulator_mode
            for _ in range(2):
                for scenario in plan.enabled_control_scenarios:
                    assert client.direct_operate([scenario.command.to_command()]).all_success


def test_packaged_protocol_trace_roundtrip() -> None:
    """The migrated wheel and paired EXE both implement the optional trace API."""
    from dnp3_master.local_outstation import LocalTestOutstation

    package = Path(os.environ["DNP3_EXPECTED_PACKAGE_ROOT"]).resolve()
    assert (package / "docs/PROTOCOL_TRACE.md").is_file()
    assert (package / "schemas/trace-result.schema.json").is_file()
    with LocalTestOutstation(package / "tools/dnp3-local-test-outstation.exe") as simulator:
        with dnp3_master.Dnp3MasterClient(dnp3_master.HostProcessConfig(
            package / "bin/dnp3-master-host.exe",
        )) as client:
            client.start_trace(dnp3_master.TraceConfig(queue_capacity=2048))
            client.connect(dnp3_master.TcpConnectionConfig(
                host="127.0.0.1", port=simulator.port,
            ))
            result = client.read([dnp3_master.ReadHeader.all_objects(30, 5)])
            assert result.summary["received_total"] == 2
            client.disconnect()
            client.stop_trace()
            directions = set()
            for _ in range(3):
                batch = client.read_trace(max_records=1024)
                directions.update(frame.direction for frame in batch.frames)
                json.dumps(batch.to_dict(), allow_nan=False)
                if batch.summary.queued_records == 0:
                    break
            else:
                raise AssertionError("bounded package trace did not drain")
            assert directions == {"RX", "TX"}


def test_copied_simulator_starter_with_isolated_wheel(tmp_path) -> None:
    """No repository import paths; exercise the actual packaged consumer suite."""
    import shutil
    import subprocess
    import sys
    from dnp3_master.local_outstation import LocalTestOutstation

    package = Path(os.environ["DNP3_EXPECTED_PACKAGE_ROOT"]).resolve()
    copied = tmp_path / "tests/dnp3"
    copied.mkdir(parents=True)
    for name in ("conftest.py", "pytest.ini", "test_basic.py", "settings.example.json"):
        shutil.copyfile(package / "examples/pytest_simulator" / name, copied / name)
    with LocalTestOutstation(package / "tools/dnp3-local-test-outstation.exe") as simulator:
        settings = json.loads((copied / "settings.example.json").read_text(encoding="utf-8"))
        settings["runtime_root"] = str(package)
        settings["connection"].update(host="127.0.0.1", port=simulator.port)
        (copied / "settings.local.json").write_text(json.dumps(settings), encoding="utf-8")
        environment = {k: v for k, v in os.environ.items() if not k.startswith("DNP3_")}
        # Preserve only the isolated wheel import path, never the source repository.
        environment["PYTHONPATH"] = os.environ["DNP3_EXPECTED_INSTALLED_ROOT"]
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        # Output is a small fixed suite, with a bounded subprocess lifetime.
        run = subprocess.run([sys.executable, "-m", "pytest", "-c", str(copied / "pytest.ini"),
                              str(copied), "-q", "-k", "not external_signal_event"],
                             cwd=tmp_path, env=environment, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        assert run.returncode == 0, (run.stdout + run.stderr)[-16384:]
        assert "14 passed" in run.stdout
