# dnp3-master-test-framework Python package

This directory is the portable pytest-facing layer. It provides the synchronous
`Dnp3MasterClient`, validated `TcpConnectionConfig`, typed process/protocol
exceptions, bounded diagnostics, and opt-in fixtures without exposing
OpenDNP3-specific C++ APIs to test cases.

The core package uses only the Python standard library. Enable the fixtures from
the consuming framework's root `conftest.py`:

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

Set `DNP3_MASTER_HOST_EXE` or pass `--dnp3-host-exe`. Full integration,
timeouts, diagnostics and copy boundaries are documented in
`docs/python_client.md`.

To request a connected fixture, also set `DNP3_OUTSTATION_HOST` (and optionally
`DNP3_OUTSTATION_PORT`, link addresses, and timeout/retry environment values),
then use `connected_master` in the test signature.
