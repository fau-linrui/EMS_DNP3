from __future__ import annotations

import os
import time

import pytest

from dnp3_master.process_metrics import (
    ProcessResourceSample,
    ProcessResourceSampler,
)


@pytest.mark.skipif(os.name != "nt", reason="supported resource source is Windows")
def test_windows_process_resource_sampler_has_explicit_same_process_scope() -> None:
    with ProcessResourceSampler(os.getpid()) as sampler:
        before = sampler.sample()
        deadline = time.perf_counter() + 0.01
        while time.perf_counter() < deadline:
            pass
        after = sampler.sample()

    assert before.source == "windows_process_api"
    assert before.scope == "dnp3_master_host_process"
    assert after.monotonic_ns >= before.monotonic_ns
    assert after.process_cpu_seconds >= before.process_cpu_seconds
    assert after.working_set_bytes > 0
    assert after.private_bytes > 0
    assert after.handle_count > 0
    assert after.thread_count > 0
    assert ProcessResourceSample.from_mapping(after.to_mapping()) == after


def test_process_resource_sample_rejects_ambiguous_fields() -> None:
    with pytest.raises(ValueError, match="fields"):
        ProcessResourceSample.from_mapping({"working_set_bytes": 1})


@pytest.mark.skipif(os.name != "nt", reason="supported resource source is Windows")
def test_repeated_thread_snapshots_do_not_leak_process_handles() -> None:
    with ProcessResourceSampler(os.getpid()) as sampler:
        baseline = sampler.sample().handle_count
        latest = baseline
        for _ in range(200):
            latest = sampler.sample().handle_count
    assert latest <= baseline + 2, f"handle_count grew from {baseline} to {latest}"
