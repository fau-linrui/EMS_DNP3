from __future__ import annotations

import pytest

from dnp3_master import (
    Dnp3MasterClient,
    PerformanceProfile,
    run_performance_suite,
    run_soak,
    write_json_report,
)


def test_read_performance_profile(
    connected_master: Dnp3MasterClient,
    performance_profile_case: PerformanceProfile,
    performance_report_directory,
) -> None:
    """Run bounded A/B reads; this test never issues a state-changing command."""

    report = run_performance_suite(
        connected_master,
        performance_profile_case,
    )
    write_json_report(
        report,
        performance_report_directory / "read-performance.json",
    )
    assert report["passed"], report


@pytest.mark.dnp3_soak
def test_read_only_soak_profile(
    connected_master: Dnp3MasterClient,
    performance_profile_case: PerformanceProfile,
    performance_report_directory,
    pytestconfig: pytest.Config,
) -> None:
    """Run the configured soak only after the operator opts in explicitly."""

    if not pytestconfig.getoption("--dnp3-run-soak"):
        pytest.skip("pass --dnp3-run-soak to start the configured soak duration")
    report = run_soak(
        connected_master,
        performance_profile_case,
        performance_report_directory / "soak",
    )
    write_json_report(
        report,
        performance_report_directory / "soak-summary.json",
    )
    assert report["passed"], report
