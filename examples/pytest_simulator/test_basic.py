"""Practical tests for the supplied EMS operation convention, not full conformance."""
import pytest

from dnp3_master import ReadHeader
from dnp3_master.simulator_suite import (
    check_read, observe_simulator_event, read_simulator_point,
    run_simulator_control, value_matches,
)

pytestmark = [pytest.mark.dnp3_dut, pytest.mark.dnp3_capability("APP.FC.01.READ")]


def test_00_connection_and_dnp3_read(sim_master):
    # The fixture has already checked installation, hello, TCP and point READ.
    assert sim_master.is_running


def test_static_point(sim_master, dnp3_simulator_settings, sim_point):
    record = read_simulator_point(sim_master, sim_point, dnp3_simulator_settings.task_timeout)
    if sim_point.expected is not None:
        assert value_matches(record.value, sim_point.expected, sim_point.tolerance), "[STATIC_VALUE] configured expectation mismatch"


def test_class_zero(sim_master, dnp3_simulator_settings):
    settings = dnp3_simulator_settings
    result = sim_master.read([ReadHeader.all_objects(60, 1)], timeout=settings.task_timeout,
                            max_measurements=settings.max_measurements, request_timeout=settings.task_timeout + 1)
    check_read(result)
    for point in settings.points:
        assert sum(point.matches(record) for record in result.measurements) == 1, f"[CLASS_ZERO] missing/duplicate {point.id}"


def test_class_event_exact_count(sim_master, dnp3_simulator_settings, sim_class_count):
    settings = dnp3_simulator_settings
    event_class, expected_count = sim_class_count
    result = sim_master.class_poll([event_class], timeout=settings.task_timeout,
                                   max_measurements=settings.max_measurements, request_timeout=settings.task_timeout + 1)
    check_read(result)
    # Zero means EXACTLY empty, not 'at least zero'. This is an EMS convention.
    assert len(result.measurements) == expected_count, "[CLASS_COUNT] response count differs from EMS convention/config"


@pytest.mark.dnp3_state_changing
@pytest.mark.dnp3_capability("APP.FC.05.DIRECT_OPERATE")
def test_control_and_feedback(sim_master, dnp3_simulator_settings, sim_control):
    run_simulator_control(sim_master, dnp3_simulator_settings, sim_control)


@pytest.mark.dnp3_capability("APP.UNSOLICITED")
def test_external_signal_event(sim_master, dnp3_simulator_settings, sim_event):
    # No simulator API, signal generator, time sync or Restart is called here.
    observe_simulator_event(sim_master, dnp3_simulator_settings, sim_event)
