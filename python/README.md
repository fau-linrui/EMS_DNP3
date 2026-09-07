# dnp3-master-test-framework Python package

This directory is the portable pytest-facing layer. Version 0.6.1 provides the
synchronous `Dnp3MasterClient`, validated TCP/read/control models, typed task
results, strict process/protocol exceptions, bounded diagnostics, PICS-aware
selection, strict EMS scenario plans, an offline configuration preflight,
continuous capture, bounded performance/soak runners, Windows process metrics,
state-change safety gates, and opt-in fixtures without exposing OpenDNP3-specific
C++ APIs to test cases.

For simulated devices, set `dnp3_simulator = true` in pytest.ini (or pass
`--dnp3-simulator`). Direct API callers use `TcpConnectionConfig(..., simulator=True)`.
This mode needs no operator/DUT identity or incident directory, permits batch/repeat
controls and optional scenario restoration, and labels pytest evidence SIMULATOR.
Protocol checks and bounded failure handling remain; LAB is the unchanged default.
See `docs/SIMULATOR_MODE.md` in the portable package/repository.

The core package uses only the Python standard library. Enable the fixtures from
the consuming framework's root `conftest.py`:

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

Portable releases include a `python-dist/*.whl`; install that wheel instead of
running pip against the manifest-protected source directory. Run the package
root `compatibility-test.ps1` first to verify the wheel, plugin, and packaged
loopback host from a blank pytest consumer without contacting a DUT.

Set `DNP3_MASTER_HOST_EXE` or pass `--dnp3-host-exe`. For real-DUT tests, also
provide a private EMS profile with `--dnp3-pics-file` and mark each test with
`dnp3_dut` plus one or more `dnp3_capability` IDs. Unknown capabilities do not
run by default.

Before any DUT connection, validate the private profile, point table, test plan,
and packaged capability matrix together:

```powershell
python -m dnp3_master.preflight `
  --pics .\config\ems.local.json `
  --points .\config\points.local.csv `
  --plan .\config\ems_test_plan.local.json `
  --capability-matrix .\config\capability_matrix.csv
```

Exit 0 means only that the offline configuration gate passed; it is not an
interoperability or conformance result. See `docs/OFFLINE_PREFLIGHT.md`.

To request a connected fixture, also set `DNP3_OUTSTATION_HOST` (and optionally
`DNP3_OUTSTATION_PORT`, link addresses, and timeout/retry environment values),
then use `connected_master` in the test signature. The client exposes
`integrity_poll`, `class_poll`, strict multi-header `read`, guarded
`select_and_operate`, response-bearing `direct_operate`, channel events and
bounded stats. State-changing tests remain skipped unless explicitly marked and
authorized with an operator ID and lab DUT ID. The bundled real-EMS templates
also require an enabled private plan entry and an exact per-run
`--dnp3-control-scenario` selection.

Use `--dnp3-performance-profile`/`DNP3_PERFORMANCE_PROFILE` for a hashed,
strict read-only performance and soak profile. The copyable
`examples/pytest_performance` suite persists immutable reports and requires an
extra `--dnp3-run-soak` before starting the configured duration. Local event
loads use `--dnp3-local-event-profile`; each benchmark chunk is capped at 4,096
events and must pass both the native capture truth check and the independent
master unsolicited-queue no-drop check. Soak runs also audit the bounded native
channel-event queue between state snapshots. These local loads are only for the
packaged loopback outstation and never constitute a DUT result.

Full integration, safety, timeout, diagnostic and copy boundaries are
documented in `docs/python_client.md` and
`docs/PERFORMANCE_AND_SOAK_GUIDE.md`.
