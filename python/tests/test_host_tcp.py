from __future__ import annotations

import os
from pathlib import Path
import socket
import threading
import time
from typing import Iterator

import pytest

from dnp3_master import (
    Dnp3MasterClient,
    HostCommandError,
    HostProcessConfig,
    TcpConnectionConfig,
)


TC_CHANNEL_TCP_CLIENT_CONNECT_001 = "TC_CHANNEL_TCP_CLIENT_CONNECT_001"
TC_CHANNEL_TCP_CLIENT_TIMEOUT_001 = "TC_CHANNEL_TCP_CLIENT_TIMEOUT_001"
TC_CHANNEL_TCP_CLIENT_SHUTDOWN_001 = "TC_CHANNEL_TCP_CLIENT_SHUTDOWN_001"
TC_CHANNEL_TCP_CLIENT_PYTEST_FIXTURE_001 = (
    "TC_CHANNEL_TCP_CLIENT_PYTEST_FIXTURE_001"
)
TC_CHANNEL_RECONNECT_FIN_001 = "TC_CHANNEL_RECONNECT_FIN_001"

pytestmark = [
    pytest.mark.dnp3_capability("CHANNEL.TCP.CLIENT"),
    pytest.mark.dnp3_capability("CHANNEL.RECONNECT"),
]


class TcpSinkServer:
    """A bounded local TCP peer; it intentionally does not emulate DNP3."""

    def __init__(self) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen()
        self._listener.settimeout(0.1)
        self.port = int(self._listener.getsockname()[1])
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._active: socket.socket | None = None
        self._connection_count = 0
        self._thread = threading.Thread(
            target=self._serve,
            name=f"dnp3-test-tcp-sink-{self.port}",
            daemon=True,
        )

    def __enter__(self) -> TcpSinkServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            connection.settimeout(0.1)
            with self._condition:
                self._active = connection
                self._connection_count += 1
                self._condition.notify_all()
            try:
                while not self._stop.is_set():
                    try:
                        data = connection.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
            finally:
                try:
                    connection.close()
                except OSError:
                    pass
                with self._condition:
                    if self._active is connection:
                        self._active = None
                    self._condition.notify_all()

    def wait_for_connections(self, count: int, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._connection_count < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def wait_for_no_active_connection(self, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._active is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close_active_connection(self) -> None:
        with self._condition:
            connection = self._active
        if connection is None:
            raise AssertionError("TCP sink has no active connection to close")
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            connection.close()
        except OSError:
            pass

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        with self._condition:
            connection = self._active
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        self._thread.join(timeout=2.0)
        assert not self._thread.is_alive(), "local TCP sink thread did not stop"


@pytest.fixture
def tcp_sink_server() -> Iterator[TcpSinkServer]:
    with TcpSinkServer() as server:
        yield server


@pytest.fixture
def dnp3_connection_config(
    tcp_sink_server: TcpSinkServer,
) -> TcpConnectionConfig:
    return connection_for(tcp_sink_server.port)


@pytest.fixture
def real_client() -> Iterator[Dnp3MasterClient]:
    configured = os.environ.get("DNP3_MASTER_HOST_EXE")
    assert configured, "DNP3_MASTER_HOST_EXE must identify the built native host"
    executable = Path(configured)
    assert executable.is_file(), f"native host does not exist: {executable}"
    client = Dnp3MasterClient(
        HostProcessConfig(
            executable=executable,
            startup_timeout=3.0,
            request_timeout=3.0,
            shutdown_timeout=3.0,
        )
    )
    client.start()
    try:
        yield client
    finally:
        diagnostics = client.close()
        assert diagnostics.cleanup_error is None, diagnostics.cleanup_error


def connection_for(port: int, *, connect_timeout: float = 2.0) -> TcpConnectionConfig:
    return TcpConnectionConfig(
        host="127.0.0.1",
        port=port,
        connect_timeout=connect_timeout,
        retry_min=0.05,
        retry_max=0.2,
        keep_alive_timeout=60.0,
    )


def has_subsequence(values: list[str], expected: list[str]) -> bool:
    position = 0
    for value in values:
        if position < len(expected) and value == expected[position]:
            position += 1
    return position == len(expected)


def collect_states(
    client: Dnp3MasterClient,
    expected: list[str],
    timeout: float = 3.0,
) -> list[str]:
    deadline = time.monotonic() + timeout
    states: list[str] = []
    while not has_subsequence(states, expected):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        batch = client.wait_event(wait_timeout=min(0.25, remaining))
        for event in batch["events"]:
            assert event["type"] == "channel_state"
            assert isinstance(event["sequence"], int)
            assert isinstance(event["monotonic_ns"], int)
            states.append(event["state"])
    assert has_subsequence(states, expected), (
        f"did not observe channel-state subsequence {expected!r}; received {states!r}"
    )
    return states


def test_tcp_connect_fin_reconnect_and_disconnect(real_client: Dnp3MasterClient) -> None:
    assert TC_CHANNEL_TCP_CLIENT_CONNECT_001
    assert TC_CHANNEL_RECONNECT_FIN_001
    with TcpSinkServer() as server:
        connected = real_client.connect(connection_for(server.port))
        assert connected["state"] == "CONNECTED"
        assert connected["channel_state"] == "OPEN"
        assert server.wait_for_connections(1)
        collect_states(real_client, ["OPENING", "OPEN"])

        status = real_client.get_status()
        assert status["state"] == "CONNECTED"
        assert status["channel"]["session_active"] is True
        with pytest.raises(HostCommandError) as duplicate:
            real_client.connect(connection_for(server.port))
        assert duplicate.value.code == "ALREADY_CONNECTED"

        server.close_active_connection()
        assert server.wait_for_connections(2), "OpenDNP3 did not reconnect after TCP FIN"
        collect_states(real_client, ["CLOSED", "OPENING", "OPEN"])
        assert real_client.get_status()["state"] == "CONNECTED"

        disconnected = real_client.disconnect()
        assert disconnected["state"] == "READY"
        collect_states(real_client, ["CLOSED", "SHUTDOWN"])
        status = real_client.get_status()
        assert status["state"] == "READY"
        assert status["channel"]["session_active"] is False
        with pytest.raises(HostCommandError) as duplicate_disconnect:
            real_client.disconnect()
        assert duplicate_disconnect.value.code == "NOT_CONNECTED"


def test_connection_timeout_cleans_up_and_allows_recovery(
    real_client: Dnp3MasterClient,
) -> None:
    assert TC_CHANNEL_TCP_CLIENT_TIMEOUT_001
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    unused_port = int(reservation.getsockname()[1])
    reservation.close()

    with pytest.raises(HostCommandError) as timed_out:
        real_client.connect(connection_for(unused_port, connect_timeout=0.2))
    assert timed_out.value.code == "CONNECTION_TIMEOUT"
    assert real_client.get_status()["state"] == "READY"

    with TcpSinkServer() as server:
        recovered = real_client.connect(connection_for(server.port))
        assert recovered["state"] == "CONNECTED"
        assert server.wait_for_connections(1)
        real_client.disconnect()


def test_process_shutdown_closes_an_active_channel() -> None:
    assert TC_CHANNEL_TCP_CLIENT_SHUTDOWN_001
    configured = os.environ.get("DNP3_MASTER_HOST_EXE")
    assert configured
    client = Dnp3MasterClient(
        HostProcessConfig(
            executable=Path(configured),
            startup_timeout=3.0,
            request_timeout=3.0,
            shutdown_timeout=3.0,
        )
    )
    with TcpSinkServer() as server:
        client.start()
        client.connect(connection_for(server.port))
        assert server.wait_for_connections(1)
        diagnostics = client.close()
        assert diagnostics.returncode == 0
        assert diagnostics.cleanup_error is None
        assert server.wait_for_no_active_connection(), (
            "host shutdown did not close the active TCP socket"
        )


def test_connected_master_fixture_is_portable(
    connected_master: Dnp3MasterClient,
    tcp_sink_server: TcpSinkServer,
) -> None:
    assert TC_CHANNEL_TCP_CLIENT_PYTEST_FIXTURE_001
    assert tcp_sink_server.wait_for_connections(1)
    assert connected_master.get_status()["state"] == "CONNECTED"
