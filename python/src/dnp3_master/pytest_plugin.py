"""Opt-in pytest fixtures for embedding the DNP3 client in another framework."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from .client import Dnp3MasterClient
from .errors import HostCommandError
from .models import HostProcessConfig, TcpConnectionConfig


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("dnp3-master")
    group.addoption(
        "--dnp3-host-exe",
        action="store",
        default=None,
        help="Path to dnp3-master-host.exe (or set DNP3_MASTER_HOST_EXE)",
    )
    group.addoption(
        "--dnp3-host-arg",
        action="append",
        default=[],
        help="Argument passed to the native host; may be repeated",
    )
    group.addoption(
        "--dnp3-startup-timeout",
        action="store",
        type=float,
        default=5.0,
        help="Seconds allowed for process start and hello",
    )
    group.addoption(
        "--dnp3-request-timeout",
        action="store",
        type=float,
        default=10.0,
        help="Default seconds allowed for one host request",
    )
    group.addoption(
        "--dnp3-shutdown-timeout",
        action="store",
        type=float,
        default=2.0,
        help="Seconds allowed for graceful shutdown before forced cleanup",
    )
    group.addoption(
        "--dnp3-outstation-host",
        action="store",
        default=None,
        help="Outstation host name/address (or set DNP3_OUTSTATION_HOST)",
    )
    group.addoption(
        "--dnp3-outstation-port",
        action="store",
        type=int,
        default=None,
        help="Outstation TCP port; defaults to the DNP3 registered port 20000",
    )
    group.addoption(
        "--dnp3-local-adapter",
        action="store",
        default=None,
        help="Local adapter address; defaults to 0.0.0.0",
    )
    group.addoption(
        "--dnp3-master-address",
        action="store",
        type=int,
        default=None,
        help="DNP3 link address of this master; defaults to 1",
    )
    group.addoption(
        "--dnp3-outstation-address",
        action="store",
        type=int,
        default=None,
        help="DNP3 link address of the outstation; defaults to 1024",
    )
    group.addoption(
        "--dnp3-connect-timeout",
        action="store",
        type=float,
        default=None,
        help="Seconds allowed for the TCP channel to reach OPEN; defaults to 5",
    )
    group.addoption(
        "--dnp3-retry-min",
        action="store",
        type=float,
        default=None,
        help="Minimum reconnect delay in seconds; defaults to 1",
    )
    group.addoption(
        "--dnp3-retry-max",
        action="store",
        type=float,
        default=None,
        help="Maximum reconnect delay in seconds; defaults to 60",
    )
    group.addoption(
        "--dnp3-keep-alive-timeout",
        action="store",
        type=float,
        default=None,
        help="DNP3 link-status keep-alive interval in seconds; defaults to 60",
    )


@pytest.fixture(scope="session")
def dnp3_host_config(pytestconfig: pytest.Config) -> HostProcessConfig:
    configured = pytestconfig.getoption("--dnp3-host-exe") or os.environ.get(
        "DNP3_MASTER_HOST_EXE"
    )
    if not configured:
        raise pytest.UsageError(
            "set DNP3_MASTER_HOST_EXE or pass --dnp3-host-exe before using DNP3 fixtures"
        )
    return HostProcessConfig(
        executable=Path(configured),
        arguments=tuple(pytestconfig.getoption("--dnp3-host-arg")),
        startup_timeout=pytestconfig.getoption("--dnp3-startup-timeout"),
        request_timeout=pytestconfig.getoption("--dnp3-request-timeout"),
        shutdown_timeout=pytestconfig.getoption("--dnp3-shutdown-timeout"),
    )


@pytest.fixture(scope="session")
def host_process(dnp3_host_config: HostProcessConfig) -> Iterator[Dnp3MasterClient]:
    client = Dnp3MasterClient(dnp3_host_config)
    client.start()
    try:
        yield client
    finally:
        diagnostics = client.close()
        if diagnostics.cleanup_error is not None:
            pytest.fail(
                "DNP3 host cleanup required forced termination: "
                f"{diagnostics.cleanup_error}"
            )


@pytest.fixture(scope="session")
def master_client(host_process: Dnp3MasterClient) -> Dnp3MasterClient:
    return host_process


def _configured_value(
    pytestconfig: pytest.Config,
    option: str,
    environment: str,
    default: object,
    converter: type[str] | type[int] | type[float],
) -> object:
    option_value = pytestconfig.getoption(option)
    raw_value = option_value if option_value is not None else os.environ.get(environment)
    if raw_value is None:
        return default
    try:
        return converter(raw_value)
    except (TypeError, ValueError) as error:
        raise pytest.UsageError(
            f"invalid {option}/{environment} value: {raw_value!r}"
        ) from error


@pytest.fixture(scope="session")
def dnp3_connection_config(pytestconfig: pytest.Config) -> TcpConnectionConfig:
    host = _configured_value(
        pytestconfig,
        "--dnp3-outstation-host",
        "DNP3_OUTSTATION_HOST",
        None,
        str,
    )
    if host is None:
        pytest.skip(
            "set DNP3_OUTSTATION_HOST or pass --dnp3-outstation-host "
            "before using connected_master"
        )

    try:
        return TcpConnectionConfig(
            host=str(host),
            port=int(
                _configured_value(
                    pytestconfig,
                    "--dnp3-outstation-port",
                    "DNP3_OUTSTATION_PORT",
                    20000,
                    int,
                )
            ),
            local_adapter=str(
                _configured_value(
                    pytestconfig,
                    "--dnp3-local-adapter",
                    "DNP3_LOCAL_ADAPTER",
                    "0.0.0.0",
                    str,
                )
            ),
            master_address=int(
                _configured_value(
                    pytestconfig,
                    "--dnp3-master-address",
                    "DNP3_MASTER_ADDRESS",
                    1,
                    int,
                )
            ),
            outstation_address=int(
                _configured_value(
                    pytestconfig,
                    "--dnp3-outstation-address",
                    "DNP3_OUTSTATION_ADDRESS",
                    1024,
                    int,
                )
            ),
            connect_timeout=float(
                _configured_value(
                    pytestconfig,
                    "--dnp3-connect-timeout",
                    "DNP3_CONNECT_TIMEOUT",
                    5.0,
                    float,
                )
            ),
            retry_min=float(
                _configured_value(
                    pytestconfig,
                    "--dnp3-retry-min",
                    "DNP3_RETRY_MIN",
                    1.0,
                    float,
                )
            ),
            retry_max=float(
                _configured_value(
                    pytestconfig,
                    "--dnp3-retry-max",
                    "DNP3_RETRY_MAX",
                    60.0,
                    float,
                )
            ),
            keep_alive_timeout=float(
                _configured_value(
                    pytestconfig,
                    "--dnp3-keep-alive-timeout",
                    "DNP3_KEEP_ALIVE_TIMEOUT",
                    60.0,
                    float,
                )
            ),
        )
    except ValueError as error:
        raise pytest.UsageError(f"invalid DNP3 connection configuration: {error}") from error


@pytest.fixture
def connected_master(
    master_client: Dnp3MasterClient,
    dnp3_connection_config: TcpConnectionConfig,
) -> Iterator[Dnp3MasterClient]:
    master_client.connect(dnp3_connection_config)
    try:
        yield master_client
    finally:
        try:
            if master_client.is_running and master_client.get_status()["state"] != "READY":
                master_client.disconnect()
        except HostCommandError as error:
            if error.code != "NOT_CONNECTED":
                raise
