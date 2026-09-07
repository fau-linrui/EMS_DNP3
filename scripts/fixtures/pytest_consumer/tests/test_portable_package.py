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
