from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Iterator

import pytest

from dnp3_master import (
    CaptureConfig,
    CapturePointRange,
    Dnp3MasterClient,
    HostProcessConfig,
    LocalEventLoad,
    PerformanceProfile,
    PerformanceProfileError,
    PerformanceThresholds,
    ReadHeader,
    ReadPerformanceScenario,
    SoakSettings,
    TcpConnectionConfig,
    load_performance_profile,
    load_local_event_profile,
    nearest_rank,
    run_performance_suite,
    run_local_event_benchmark,
    run_soak,
)
from dnp3_master.local_outstation import LocalTestOutstation


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PROFILE = REPOSITORY_ROOT / "config" / "performance_profile.example.json"
EXAMPLE_EVENT_PROFILE = REPOSITORY_ROOT / "config" / "local_event_profile.example.json"
TC_APP_LARGE_POINT_PERFORMANCE_LOCAL_001 = (
    "TC_APP_LARGE_POINT_PERFORMANCE_LOCAL_001"
)


def _profile(point_count: int = 4096) -> PerformanceProfile:
    headers = (
        ReadHeader.all_objects(1, 2),
        ReadHeader.all_objects(30, 5),
        ReadHeader.all_objects(10, 2),
        ReadHeader.all_objects(40, 3),
    )
    expected = point_count * 4
    baseline = ReadPerformanceScenario(
        scenario_id="large-static-baseline",
        operation="read",
        headers=headers,
        classes=(),
        capture=None,
        warmup_iterations=0,
        measurement_iterations=2,
        interval_seconds=0.0,
        cooldown_seconds=0.0,
        task_timeout_seconds=5.0,
        max_measurements=expected,
        expected_objects_per_iteration=expected,
        expected_by_kind={
            "analog_input": point_count,
            "analog_output_status": point_count,
            "binary_input": point_count,
            "binary_output_status": point_count,
        },
        expected_by_group_variation={
            "1:2": point_count,
            "10:2": point_count,
            "30:5": point_count,
            "40:3": point_count,
        },
    )
    captured = replace(
        baseline,
        scenario_id="large-static-captured",
        capture=CaptureConfig(
            mode="static_set",
            sources=("solicited",),
            duration_limit=6.0,
            point_ranges=(
                CapturePointRange("binary_input", 0, point_count - 1),
                CapturePointRange("analog_input", 0, point_count - 1),
                CapturePointRange("binary_output_status", 0, point_count - 1),
                CapturePointRange("analog_output_status", 0, point_count - 1),
            ),
            queue_capacity=min(65536, expected * 2),
        ),
        baseline_scenario_id=baseline.scenario_id,
    )
    thresholds = PerformanceThresholds(
        minimum_samples=2,
        max_task_p95_ms=5000,
        max_task_p99_ms=5000,
        max_task_ms=5000,
        max_cpu_core_percent=10000,
        max_working_set_bytes=2 * 1024**3,
        max_private_bytes=2 * 1024**3,
        max_handle_count=10000,
        max_thread_count=1000,
        max_private_growth_bytes_per_hour=1e18,
        max_working_set_growth_bytes_per_hour=1e18,
        max_handle_growth=1000,
        max_thread_growth=1000,
        max_capture_overhead_percent=1000,
    )
    soak = SoakSettings(
        target_duration_seconds=0.35,
        cycle_interval_seconds=0.01,
        checkpoint_interval_seconds=0.1,
        resource_sample_interval_seconds=0.1,
        watchdog_timeout_seconds=15.0,
        max_checkpoints=3,
        max_evidence_bytes=256 * 1024,
        min_free_disk_bytes=0,
        allowed_reconnects=0,
        max_consecutive_failures=0,
    )
    return PerformanceProfile(
        profile_id="pytest-local-large-static",
        scope="LOCAL_LOOPBACK_ONLY",
        seed=20260831,
        scenarios=(baseline, captured),
        thresholds=thresholds,
        soak=soak,
    )


@pytest.fixture
def large_loopback_stack(
    tmp_path: Path,
) -> Iterator[tuple[Dnp3MasterClient, LocalTestOutstation]]:
    host = os.environ.get("DNP3_MASTER_HOST_EXE")
    outstation = os.environ.get("DNP3_TEST_OUTSTATION_EXE")
    assert host and outstation
    with LocalTestOutstation(
        Path(outstation),
        point_count=4096,
        event_buffer_capacity=8192,
        startup_timeout=10.0,
    ) as local:
        with Dnp3MasterClient(
            HostProcessConfig(
                executable=Path(host),
                safety_incident_directory=tmp_path / "safety-incidents",
                startup_timeout=3.0,
                request_timeout=10.0,
                shutdown_timeout=3.0,
            )
        ) as client:
            client.connect(
                TcpConnectionConfig(
                    host="127.0.0.1",
                    port=local.port,
                    connect_timeout=3.0,
                    retry_min=0.05,
                    retry_max=0.2,
                    master_address=1,
                    outstation_address=1024,
                )
            )
            try:
                yield client, local
            finally:
                if client.is_running:
                    client.disconnect()


def test_example_profile_is_strict_hashed_and_24_hour_configurable() -> None:
    profile = load_performance_profile(EXAMPLE_PROFILE)
    assert profile.source_sha256 == hashlib.sha256(EXAMPLE_PROFILE.read_bytes()).hexdigest()
    assert profile.soak.target_duration_seconds == 24 * 60 * 60
    assert profile.scenarios[1].baseline_scenario_id == profile.scenarios[0].scenario_id
    assert profile.scenarios[1].expected_objects_per_iteration == 4096
    assert profile.scenarios[1].expected_by_kind["analog_input"] == 1024
    assert "OBJ.G30.V5" in profile.required_capability_ids
    assert "QUAL.Q06.REVIEW" in profile.required_capability_ids


def test_profile_rejects_duplicate_keys(tmp_path: Path) -> None:
    text = EXAMPLE_PROFILE.read_text(encoding="utf-8")
    duplicate = text.replace(
        '"schema_version": 1,',
        '"schema_version": 1,\n  "profile_id": "duplicate",',
        1,
    )
    path = tmp_path / "duplicate.json"
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(PerformanceProfileError, match="duplicate JSON key"):
        load_performance_profile(path)


def test_local_event_profile_binds_startup_sizing_and_source_hash() -> None:
    profile = load_local_event_profile(EXAMPLE_EVENT_PROFILE)
    assert profile.outstation_point_count == 1024
    assert profile.event_buffer_capacity == 8192
    assert {load.interval_us for load in profile.loads} == {0, 1000}
    assert profile.source_sha256 == hashlib.sha256(
        EXAMPLE_EVENT_PROFILE.read_bytes()
    ).hexdigest()


def test_local_event_load_cannot_overrun_master_unsolicited_queue() -> None:
    with pytest.raises(ValueError, match="4096-object master unsolicited queue"):
        LocalEventLoad(
            scenario_id="too-large-event-chunk",
            point_type="analog_input",
            event_count=4097,
            iterations=1,
            seed=1,
            start_sequence=0,
            timestamp_base_ms=1700000000000,
            point_span=4097,
            interval_us=1,
            capture_duration_seconds=2.0,
            completion_timeout_seconds=2.0,
            queue_capacity=8192,
            max_elapsed_seconds=2.0,
            max_cpu_core_percent=10000.0,
        )


def test_soak_watchdog_must_cover_all_bounded_capture_rpcs() -> None:
    profile = _profile()
    with pytest.raises(PerformanceProfileError, match="begin/read/end RPC budget"):
        replace(
            profile,
            soak=replace(profile.soak, watchdog_timeout_seconds=14.0),
        )


def test_nearest_rank_has_a_fixed_small_population_definition() -> None:
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 95) == 4.0
    assert nearest_rank([], 99) is None


def test_local_event_truth_uses_post_wire_float32_values() -> None:
    local = LocalTestOutstation("not-started.exe", point_count=16)
    truth = local.plan_events(
        "analog_input",
        20,
        scenario_id="float32-truth",
        seed=7,
        timestamp_base_ms=1700000100000,
        point_span=16,
    )
    assert truth.records_sha256 == (
        "0c872ca488d54e7fd14f1d6cfe227bc31927b9dff2ad1d814a13236a94eb878b"
    )


@pytest.mark.dnp3_performance
def test_large_point_table_capture_ab_benchmark(
    large_loopback_stack: tuple[Dnp3MasterClient, LocalTestOutstation],
) -> None:
    assert TC_APP_LARGE_POINT_PERFORMANCE_LOCAL_001
    client, _ = large_loopback_stack
    report = run_performance_suite(client, _profile())
    assert report["passed"] is True, report
    assert report["formal_dut_conclusion"] is False
    assert len(report["scenarios"]) == 2
    for scenario in report["scenarios"]:
        assert scenario["completed_iterations"] == 2
        assert scenario["received_objects"] == 32768
        assert scenario["expected_by_kind"]["analog_input"] == 4096
        assert scenario["received_by_kind"]["analog_input"] == 8192
        assert scenario["received_by_group_variation"]["30:5"] == 8192
        assert scenario["resources"]["available"] is True
        assert scenario["network_rx_bytes"]["value"] is None
        assert scenario["dut_resources"]["value"] is None
    baseline, captured = report["scenarios"]
    assert baseline["capture"] == {
        "available": False,
        "reason": "CAPTURE_DISABLED_FOR_BASELINE",
    }
    assert captured["capture"]["completed_runs"] == 2
    assert captured["capture"]["valid_runs"] == 2
    assert captured["capture"]["expected_total"] == 32768
    assert captured["capture"]["received_unique"] == 32768
    assert captured["capture"]["missing"] == 0
    assert captured["capture"]["duplicates"] == 0
    assert captured["capture"]["queue_overflow"] == 0
    assert report["capture_overhead"][0]["passed"] is True


@pytest.mark.dnp3_performance
def test_large_point_table_rejects_same_total_with_wrong_object_mix(
    large_loopback_stack: tuple[Dnp3MasterClient, LocalTestOutstation],
) -> None:
    client, _ = large_loopback_stack
    profile = _profile()
    scenario = replace(
        profile.scenarios[0],
        measurement_iterations=1,
        expected_by_kind={"analog_input": 16384},
    )
    report = run_performance_suite(
        client,
        replace(
            profile,
            scenarios=(scenario,),
            thresholds=replace(profile.thresholds, minimum_samples=1),
        ),
    )
    assert report["passed"] is False
    assert "by_kind mismatch" in report["scenarios"][0]["failures"][0]


@pytest.mark.dnp3_performance
@pytest.mark.parametrize("interval_us", [0, 1000])
def test_deterministic_burst_and_paced_event_benchmarks(
    large_loopback_stack: tuple[Dnp3MasterClient, LocalTestOutstation],
    interval_us: int,
) -> None:
    client, outstation = large_loopback_stack
    enabled = client.enable_unsolicited((2,), timeout=3.0)
    assert enabled.task_status == "SUCCESS"
    load = LocalEventLoad(
        scenario_id=f"local-events-{interval_us}",
        point_type="analog_input",
        event_count=4096,
        iterations=1,
        seed=20260831,
        start_sequence=1000,
        timestamp_base_ms=1700000200000,
        point_span=128,
        interval_us=interval_us,
        capture_duration_seconds=15.0,
        completion_timeout_seconds=10.0,
        queue_capacity=8192,
        max_elapsed_seconds=10.0,
        max_cpu_core_percent=10000.0,
    )
    report = run_local_event_benchmark(client, outstation, load)
    assert report["failure"] is None, report["failure"]
    assert all(
        item["passed"] for item in report["threshold_checks"]
    ), report["threshold_checks"]
    assert report["passed"] is True, report
    assert report["events_expected"] == 4096
    assert report["events_received"] == 4096
    assert report["queue_overflow"] == 0
    assert report["unsolicited_queue"]["events_drained"] == 4096
    assert report["unsolicited_queue"]["dropped_total"] == 0
    assert report["unsolicited_queue"]["queue_capacity"] == 4096
    assert report["formal_dut_conclusion"] is False
    assert report["network_bytes"]["value"] is None
    assert report["mode"] == (
        "burst" if interval_us == 0 else "paced_continuous_chunk"
    )


@pytest.mark.dnp3_soak
def test_short_soak_rotates_atomic_checkpoints_and_finishes(
    large_loopback_stack: tuple[Dnp3MasterClient, LocalTestOutstation],
    tmp_path: Path,
) -> None:
    client, _ = large_loopback_stack
    profile = _profile()
    baseline = replace(
        profile.scenarios[0],
        measurement_iterations=1,
    )
    profile = replace(
        profile,
        scenarios=(baseline,),
        thresholds=replace(profile.thresholds, minimum_samples=1),
    )
    report = run_soak(client, profile, tmp_path / "soak")
    assert report["status"] == "COMPLETED"
    assert report["passed"] is True
    assert report["cycle_count"] > 0
    assert report["channel_events"]["dropped_total"] == 0
    assert report["channel_events"]["last_state"] == "OPEN"
    evidence = Path(report["evidence_directory"])
    checkpoint_paths = sorted(evidence.glob("checkpoint-[0-9]*.json"))
    assert 1 <= len(checkpoint_paths) <= profile.soak.max_checkpoints
    anchor_path = evidence / "checkpoint-anchor.json"
    assert anchor_path.is_file()
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    retained_sequences = [
        json.loads(path.read_text(encoding="utf-8"))["checkpoint_sequence"]
        for path in checkpoint_paths
    ]
    assert anchor["last_removed_sequence"] < anchor["next_sequence"]
    assert anchor["next_sequence"] == min(retained_sequences)
    final_path = Path(report["final_report_path"])
    persisted = json.loads(final_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "COMPLETED"
    newest = max(
        checkpoint_paths,
        key=lambda item: json.loads(item.read_text(encoding="utf-8"))[
            "checkpoint_sequence"
        ],
    )
    assert hashlib.sha256(newest.read_bytes()).hexdigest() == report[
        "checkpoint_chain_tail_sha256"
    ]


@pytest.mark.dnp3_soak
def test_soak_interruption_is_never_reported_as_passed(
    large_loopback_stack: tuple[Dnp3MasterClient, LocalTestOutstation],
    tmp_path: Path,
) -> None:
    client, _ = large_loopback_stack
    profile = _profile()
    profile = replace(profile, scenarios=(profile.scenarios[0],))
    report = run_soak(
        client,
        profile,
        tmp_path / "interrupted",
        stop_requested=lambda: True,
    )
    assert report["status"] == "INCOMPLETE_INTERRUPTED"
    assert report["passed"] is False
    assert Path(report["final_report_path"]).is_file()
