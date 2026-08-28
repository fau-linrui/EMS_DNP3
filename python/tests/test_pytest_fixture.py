from __future__ import annotations

from dnp3_master import Dnp3MasterClient


def test_portable_session_fixtures_share_started_client(
    host_process: Dnp3MasterClient,
    master_client: Dnp3MasterClient,
) -> None:
    assert host_process is master_client
    assert master_client.is_running
    assert master_client.hello_info["backend"] == "opendnp3"
    assert master_client.get_status()["state"] == "READY"
