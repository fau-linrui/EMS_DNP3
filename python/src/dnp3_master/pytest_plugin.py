"""Opt-in pytest fixtures for embedding the DNP3 client in another framework."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
from typing import Iterator, Mapping

import pytest

from .client import Dnp3MasterClient
from .evidence import EvidenceRecorder
from .errors import HostCommandError
from .models import HostProcessConfig, LabSafetyConfig, TcpConnectionConfig
from .point_table import PointTable, PointTableError, load_point_table


_VALID_PICS_STATUSES = frozenset({"SUPPORTED", "NOT_SUPPORTED", "UNKNOWN"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_CAPABILITY_ID_PATTERN = re.compile(r"^[A-Z0-9]+(?:[._-][A-Z0-9]+)*$")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


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
        "--dnp3-pics-file",
        action="store",
        default=None,
        help=(
            "Machine-readable EMS capability profile JSON; may also be set with "
            "DNP3_PICS_FILE"
        ),
    )
    group.addoption(
        "--dnp3-capability-matrix",
        action="store",
        default=None,
        help=(
            "Capability matrix used to reject unknown/typoed PICS IDs; normally "
            "auto-detected from config/capability_matrix.csv"
        ),
    )
    group.addoption(
        "--dnp3-points-file",
        action="store",
        default=None,
        help="Private strict point-table CSV (or set DNP3_POINTS_FILE)",
    )
    group.addoption(
        "--dnp3-evidence-dir",
        action="store",
        default=None,
        help=(
            "Create a redacted run manifest below this directory "
            "(or set DNP3_EVIDENCE_DIR)"
        ),
    )
    group.addoption(
        "--dnp3-safety-incident-dir",
        action="store",
        default=None,
        help=(
            "Persistent uncertain-control lock directory (or set "
            "DNP3_SAFETY_INCIDENT_DIR); defaults to "
            "evidence/local/safety-incidents"
        ),
    )
    group.addoption(
        "--dnp3-unknown-policy",
        action="store",
        choices=("xfail", "skip", "error"),
        default=None,
        help=(
            "Action for DUT capabilities absent from the PICS or marked UNKNOWN; "
            "defaults to xfail"
        ),
    )
    group.addoption(
        "--dnp3-allow-state-changing",
        action="store_true",
        default=False,
        help=(
            "Explicitly authorize tests marked dnp3_state_changing in the isolated "
            "test environment"
        ),
    )
    group.addoption(
        "--dnp3-operator-id",
        action="store",
        default=None,
        help="Auditable operator ID required when state-changing tests are unlocked",
    )
    group.addoption(
        "--dnp3-dut-id",
        action="store",
        default=None,
        help="Auditable lab DUT ID required when state-changing tests are unlocked",
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


def _load_capability_ids(path: Path) -> frozenset[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or "capability_id" not in reader.fieldnames:
                raise pytest.UsageError(
                    "DNP3 capability matrix must contain a capability_id column"
                )
            capability_ids: set[str] = set()
            for line_number, row in enumerate(reader, start=2):
                capability_id = row.get("capability_id", "")
                if not _CAPABILITY_ID_PATTERN.fullmatch(capability_id):
                    raise pytest.UsageError(
                        "DNP3 capability matrix has an invalid capability_id at "
                        f"line {line_number}: {capability_id!r}"
                    )
                if capability_id in capability_ids:
                    raise pytest.UsageError(
                        "DNP3 capability matrix repeats capability_id "
                        f"{capability_id!r}"
                    )
                capability_ids.add(capability_id)
    except pytest.UsageError:
        raise
    except (OSError, csv.Error) as error:
        raise pytest.UsageError(
            f"cannot read DNP3 capability matrix {path}: {error}"
        ) from error
    if not capability_ids:
        raise pytest.UsageError("DNP3 capability matrix must not be empty")
    return frozenset(capability_ids)


def _auto_capability_matrix_path() -> Path | None:
    candidates = (
        Path.cwd() / "config" / "capability_matrix.csv",
        Path(__file__).resolve().parents[3] / "config" / "capability_matrix.csv",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _load_pics_capabilities(
    path: Path,
    known_capability_ids: frozenset[str] | None = None,
) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise pytest.UsageError(f"cannot read DNP3 PICS file {path}: {error}") from error
    try:
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except (json.JSONDecodeError, ValueError) as error:
        raise pytest.UsageError(f"DNP3 PICS file is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise pytest.UsageError("DNP3 PICS root must be a JSON object")
    if document.get("schema_version") != 1:
        raise pytest.UsageError("DNP3 PICS schema_version must be 1")
    unknown_root_fields = set(document).difference(
        {"schema_version", "device", "capabilities", "notes"}
    )
    if unknown_root_fields:
        raise pytest.UsageError(
            "DNP3 PICS has unknown root fields: "
            + ", ".join(sorted(unknown_root_fields))
        )
    capabilities = document.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        raise pytest.UsageError(
            "DNP3 PICS capabilities must be a non-empty JSON object"
        )

    normalized: dict[str, str] = {}
    for capability_id, status in capabilities.items():
        if not isinstance(capability_id, str) or not _CAPABILITY_ID_PATTERN.fullmatch(
            capability_id
        ):
            raise pytest.UsageError(
                "DNP3 PICS capability IDs must use the capability-matrix ID syntax"
            )
        if status not in _VALID_PICS_STATUSES:
            raise pytest.UsageError(
                f"DNP3 PICS capability {capability_id!r} has invalid status {status!r}"
            )
        if (
            known_capability_ids is not None
            and capability_id not in known_capability_ids
        ):
            raise pytest.UsageError(
                f"DNP3 PICS capability {capability_id!r} is not present in the "
                "capability matrix"
            )
        normalized[capability_id] = status

    device = document.get("device")
    required_device_fields = {"vendor", "model", "firmware", "profile_revision"}
    if not isinstance(device, dict) or set(device) != required_device_fields:
        raise pytest.UsageError(
            "DNP3 PICS device must contain exactly vendor, model, firmware, "
            "and profile_revision"
        )
    for field_name, field_value in device.items():
        if (
            not isinstance(field_value, str)
            or not field_value
            or len(field_value) > 256
        ):
            raise pytest.UsageError(
                f"DNP3 PICS device field {field_name!r} must contain 1-256 characters"
            )
    notes = document.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > 4096):
        raise pytest.UsageError("DNP3 PICS notes must be a string of at most 4096 characters")
    return normalized


def pytest_configure(config: pytest.Config) -> None:
    for marker in (
        "dnp3_capability(name): trace a test to one capability-matrix identifier",
        "dnp3_dut: apply PICS capability gating before touching a real DUT",
        "dnp3_unsupported_behavior: run only when at least one declared DUT capability is NOT_SUPPORTED",
        "dnp3_state_changing: require explicit authorization because the test may change DUT state",
    ):
        config.addinivalue_line("markers", marker)

    configured = config.getoption("--dnp3-pics-file") or os.environ.get(
        "DNP3_PICS_FILE"
    )
    configured_matrix = config.getoption(
        "--dnp3-capability-matrix"
    ) or os.environ.get("DNP3_CAPABILITY_MATRIX")
    capabilities: dict[str, str] | None = None
    pics_path: Path | None = None
    matrix_path: Path | None = None
    capability_ids: frozenset[str] | None = None
    if configured:
        pics_path = Path(configured).expanduser().resolve(strict=False)
        matrix_path = (
            Path(configured_matrix).expanduser().resolve(strict=False)
            if configured_matrix
            else _auto_capability_matrix_path()
        )
        if matrix_path is None:
            raise pytest.UsageError(
                "a DNP3 PICS file requires config/capability_matrix.csv; pass "
                "--dnp3-capability-matrix or set DNP3_CAPABILITY_MATRIX"
            )
        capability_ids = _load_capability_ids(matrix_path)
        capabilities = _load_pics_capabilities(pics_path, capability_ids)
    setattr(config, "_dnp3_pics_capabilities", capabilities)
    setattr(config, "_dnp3_pics_path", pics_path)
    setattr(config, "_dnp3_capability_ids", capability_ids)
    setattr(config, "_dnp3_capability_matrix_path", matrix_path)

    configured_points = config.getoption("--dnp3-points-file") or os.environ.get(
        "DNP3_POINTS_FILE"
    )
    point_table: PointTable | None = None
    points_path: Path | None = None
    if configured_points:
        points_path = Path(configured_points).expanduser().resolve(strict=False)
        try:
            point_table = load_point_table(points_path)
        except PointTableError as error:
            raise pytest.UsageError(f"invalid DNP3 point table: {error}") from error
    setattr(config, "_dnp3_point_table", point_table)
    setattr(config, "_dnp3_points_path", points_path)

    evidence_recorder: EvidenceRecorder | None = None
    configured_evidence = config.getoption("--dnp3-evidence-dir") or os.environ.get(
        "DNP3_EVIDENCE_DIR"
    )
    if configured_evidence:
        evidence_matrix = matrix_path
        if evidence_matrix is None:
            evidence_matrix = (
                Path(configured_matrix).expanduser().resolve(strict=False)
                if configured_matrix
                else _auto_capability_matrix_path()
            )
        repository_root = (
            evidence_matrix.parent.parent
            if evidence_matrix is not None
            else Path.cwd() if (Path.cwd() / ".git").exists() else None
        )
        configured_host = config.getoption("--dnp3-host-exe") or os.environ.get(
            "DNP3_MASTER_HOST_EXE"
        )
        try:
            evidence_recorder = EvidenceRecorder(
                Path(configured_evidence),
                repository_root=repository_root,
                execution_root=Path(config.rootpath),
                host_executable=(
                    Path(configured_host) if configured_host else None
                ),
                inputs={
                    "capability_matrix": evidence_matrix,
                    "pics": pics_path,
                    "point_table": points_path,
                },
                runner={"pytest_version": pytest.__version__},
            )
        except (OSError, ValueError) as error:
            raise pytest.UsageError(
                f"cannot initialize DNP3 evidence directory: {error}"
            ) from error
    setattr(config, "_dnp3_evidence_recorder", evidence_recorder)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[object]
):
    outcome = yield
    report = outcome.get_result()
    recorder: EvidenceRecorder | None = getattr(
        item.config, "_dnp3_evidence_recorder", None
    )
    if recorder is None:
        return
    markers = tuple(marker.name for marker in item.iter_markers())
    capabilities = tuple(
        str(marker.args[0])
        for marker in item.iter_markers(name="dnp3_capability")
        if marker.args
    )
    captured = "\n".join(
        value
        for value in (
            getattr(report, "capstdout", ""),
            getattr(report, "capstderr", ""),
            getattr(report, "caplog", ""),
        )
        if value
    )
    recorder.record_phase(
        nodeid=report.nodeid,
        phase=report.when,
        outcome=report.outcome,
        duration_seconds=report.duration,
        markers=markers,
        capabilities=capabilities,
        was_xfail=(
            str(report.wasxfail) if hasattr(report, "wasxfail") else None
        ),
        failure=report.longreprtext if report.failed else "",
        captured_output=captured,
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    recorder: EvidenceRecorder | None = getattr(
        session.config, "_dnp3_evidence_recorder", None
    )
    if recorder is not None:
        recorder.finalize(int(exitstatus))


def pytest_terminal_summary(terminalreporter: object) -> None:
    config = getattr(terminalreporter, "config", None)
    recorder: EvidenceRecorder | None = getattr(
        config, "_dnp3_evidence_recorder", None
    )
    if recorder is not None:
        terminalreporter.write_line(
            f"DNP3 evidence: {recorder.run_directory / 'manifest.json'}"
        )


def _unknown_policy(config: pytest.Config) -> str:
    configured = config.getoption("--dnp3-unknown-policy") or os.environ.get(
        "DNP3_UNKNOWN_POLICY", "xfail"
    )
    if configured not in {"xfail", "skip", "error"}:
        raise pytest.UsageError(
            "DNP3_UNKNOWN_POLICY must be one of: xfail, skip, error"
        )
    return configured


def _state_changing_authorized(config: pytest.Config) -> bool:
    if bool(config.getoption("--dnp3-allow-state-changing")):
        return True
    return os.environ.get("DNP3_ALLOW_STATE_CHANGING", "").strip().lower() in _TRUE_VALUES


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    capabilities: Mapping[str, str] | None = getattr(
        config, "_dnp3_pics_capabilities", None
    )
    known_capability_ids: frozenset[str] | None = getattr(
        config, "_dnp3_capability_ids", None
    )
    policy = _unknown_policy(config)
    state_changing_authorized = _state_changing_authorized(config)
    collection_errors: list[str] = []

    for item in items:
        state_changing_test = item.get_closest_marker("dnp3_state_changing") is not None
        dut_test = item.get_closest_marker("dnp3_dut") is not None
        if state_changing_test and not dut_test:
            collection_errors.append(
                f"{item.nodeid}: dnp3_state_changing tests must also use dnp3_dut "
                "and declare capability markers"
            )
            continue
        if state_changing_test and not state_changing_authorized:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "state-changing DNP3 test is locked; pass "
                        "--dnp3-allow-state-changing only for an authorized lab DUT"
                    )
                )
            )
        elif state_changing_test:
            operator_id = config.getoption("--dnp3-operator-id") or os.environ.get(
                "DNP3_OPERATOR_ID"
            )
            dut_id = config.getoption("--dnp3-dut-id") or os.environ.get(
                "DNP3_DUT_ID"
            )
            if not operator_id or not dut_id:
                collection_errors.append(
                    f"{item.nodeid}: authorized dnp3_state_changing tests require "
                    "--dnp3-operator-id and --dnp3-dut-id (or matching environment variables)"
                )
                continue

        if not dut_test:
            continue

        capability_ids: list[str] = []
        invalid_marker = False
        for marker in item.iter_markers("dnp3_capability"):
            if len(marker.args) != 1 or not isinstance(marker.args[0], str) or not marker.args[0]:
                collection_errors.append(
                    f"{item.nodeid}: dnp3_capability requires one non-empty string"
                )
                invalid_marker = True
                break
            capability_id = marker.args[0]
            if not _CAPABILITY_ID_PATTERN.fullmatch(capability_id):
                collection_errors.append(
                    f"{item.nodeid}: dnp3_capability has invalid ID syntax: "
                    f"{capability_id!r}"
                )
                invalid_marker = True
                break
            if (
                known_capability_ids is not None
                and capability_id not in known_capability_ids
            ):
                collection_errors.append(
                    f"{item.nodeid}: dnp3_capability ID is not present in the "
                    f"capability matrix: {capability_id!r}"
                )
                invalid_marker = True
                break
            capability_ids.append(capability_id)
        if invalid_marker:
            continue
        if not capability_ids:
            collection_errors.append(
                f"{item.nodeid}: dnp3_dut tests require at least one dnp3_capability marker"
            )
            continue

        statuses = {
            capability_id: (
                capabilities.get(capability_id, "UNKNOWN")
                if capabilities is not None
                else "UNKNOWN"
            )
            for capability_id in capability_ids
        }
        unknown = [
            capability_id
            for capability_id, status in statuses.items()
            if status == "UNKNOWN"
        ]
        if unknown:
            reason = "DUT capability is UNKNOWN: " + ", ".join(sorted(unknown))
            if capabilities is None:
                reason += "; no --dnp3-pics-file/DNP3_PICS_FILE was supplied"
            if policy == "error":
                collection_errors.append(f"{item.nodeid}: {reason}")
            elif policy == "skip":
                item.add_marker(pytest.mark.skip(reason=reason))
            else:
                item.add_marker(pytest.mark.xfail(reason=reason, run=False, strict=False))
            continue

        not_supported = [
            capability_id
            for capability_id, status in statuses.items()
            if status == "NOT_SUPPORTED"
        ]
        unsupported_test = item.get_closest_marker("dnp3_unsupported_behavior") is not None
        if not_supported and not unsupported_test:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "positive DUT test is not applicable because PICS declares "
                        "NOT_SUPPORTED: " + ", ".join(sorted(not_supported))
                    )
                )
            )
        elif unsupported_test and not not_supported:
            item.add_marker(
                pytest.mark.skip(
                    reason="unsupported-behavior test requires a NOT_SUPPORTED PICS capability"
                )
            )

    if collection_errors:
        raise pytest.UsageError("\n".join(collection_errors))


@pytest.fixture(scope="session")
def dnp3_pics(pytestconfig: pytest.Config) -> Mapping[str, str]:
    """Return the validated capability-status mapping for DUT-aware tests."""

    capabilities = getattr(pytestconfig, "_dnp3_pics_capabilities", None)
    return {} if capabilities is None else dict(capabilities)


@pytest.fixture(scope="session")
def dnp3_point_table(pytestconfig: pytest.Config) -> PointTable | None:
    """Return the validated private point table, if one was configured."""

    return getattr(pytestconfig, "_dnp3_point_table", None)


@pytest.fixture(scope="session")
def dnp3_host_config(pytestconfig: pytest.Config) -> HostProcessConfig:
    configured = pytestconfig.getoption("--dnp3-host-exe") or os.environ.get(
        "DNP3_MASTER_HOST_EXE"
    )
    if not configured:
        raise pytest.UsageError(
            "set DNP3_MASTER_HOST_EXE or pass --dnp3-host-exe before using DNP3 fixtures"
        )
    incident_directory = pytestconfig.getoption(
        "--dnp3-safety-incident-dir"
    ) or os.environ.get("DNP3_SAFETY_INCIDENT_DIR")
    if not incident_directory:
        incident_directory = Path.cwd() / "evidence" / "local" / "safety-incidents"
    return HostProcessConfig(
        executable=Path(configured),
        arguments=tuple(pytestconfig.getoption("--dnp3-host-arg")),
        safety_incident_directory=Path(incident_directory),
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


@pytest.fixture
def dnp3_connection_config(
    pytestconfig: pytest.Config,
    request: pytest.FixtureRequest,
) -> TcpConnectionConfig:
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

    safety: LabSafetyConfig | None = None
    state_changing_test = (
        request.node.get_closest_marker("dnp3_state_changing") is not None
    )
    if state_changing_test and _state_changing_authorized(pytestconfig):
        operator_id = pytestconfig.getoption("--dnp3-operator-id") or os.environ.get(
            "DNP3_OPERATOR_ID"
        )
        dut_id = pytestconfig.getoption("--dnp3-dut-id") or os.environ.get(
            "DNP3_DUT_ID"
        )
        if not operator_id or not dut_id:
            raise pytest.UsageError(
                "state-changing DNP3 tests require --dnp3-operator-id and "
                "--dnp3-dut-id (or DNP3_OPERATOR_ID and DNP3_DUT_ID)"
            )
        safety = LabSafetyConfig(
            operator_id=str(operator_id),
            dut_id=str(dut_id),
            allow_state_change=True,
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
            safety=safety,
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
