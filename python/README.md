# dnp3-master-test-framework Python package

This directory is the portable pytest-facing layer. Version 0.3.0 provides the
synchronous `Dnp3MasterClient`, validated TCP/read/control models, typed task
results, strict process/protocol exceptions, bounded diagnostics, PICS-aware
selection, state-change safety gates, and opt-in fixtures without exposing
OpenDNP3-specific C++ APIs to test cases.

The core package uses only the Python standard library. Enable the fixtures from
the consuming framework's root `conftest.py`:

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

Set `DNP3_MASTER_HOST_EXE` or pass `--dnp3-host-exe`. For real-DUT tests, also
provide a private EMS profile with `--dnp3-pics-file` and mark each test with
`dnp3_dut` plus one or more `dnp3_capability` IDs. Unknown capabilities do not
run by default.

To request a connected fixture, also set `DNP3_OUTSTATION_HOST` (and optionally
`DNP3_OUTSTATION_PORT`, link addresses, and timeout/retry environment values),
then use `connected_master` in the test signature. The client exposes
`integrity_poll`, `class_poll`, strict multi-header `read`, guarded
`select_and_operate`, response-bearing `direct_operate`, channel events and
bounded stats. State-changing tests remain skipped unless explicitly marked and
authorized with an operator ID and lab DUT ID.

Full integration, safety, timeout, diagnostic and copy boundaries are
documented in `docs/python_client.md` and
`docs/BEGINNER_MIGRATION_BUILD_USE_GUIDE.md`.
