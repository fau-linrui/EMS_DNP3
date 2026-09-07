"""Copy this directory together; only settings.local.json needs site edits."""
import pytest

from dnp3_master.simulator_suite import simulator_session

def pytest_generate_tests(metafunc):
    settings = getattr(metafunc.config, "_dnp3_simulator_settings", None)
    if settings is None:
        raise pytest.UsageError("use this directory's pytest.ini and supply settings.local.json")
    static = {"BI": "OBJ.G1.V2", "AI": "OBJ.G30.V5", "BO": "OBJ.G10.V2", "AO": "OBJ.G40.V3"}
    # The setup probe always reads the first configured point.
    metafunc.definition.add_marker(pytest.mark.dnp3_capability(static[settings.points[0].kind]))
    metafunc.definition.add_marker(pytest.mark.dnp3_capability("QUAL.Q01.REVIEW"))
    if metafunc.function.__name__ == "test_class_zero":
        for point in settings.points:
            metafunc.definition.add_marker(pytest.mark.dnp3_capability(static[point.kind]))
        metafunc.definition.add_marker(pytest.mark.dnp3_capability("OBJ.G60.V1"))
    for name, rows in (
        ("sim_point", settings.points), ("sim_control", settings.controls),
        ("sim_event", settings.events), ("sim_class_count", settings.class_counts),
    ):
        if name in metafunc.fixturenames:
            parameters = []
            for row in rows:
                if name == "sim_point":
                    capabilities = [static[row.kind]]
                elif name == "sim_control":
                    capabilities = [static[row.feedback.kind], "OBJ.G12.V1" if row.kind == "BO" else "OBJ.G41.V3"]
                elif name == "sim_event":
                    capabilities = [static[row.point.kind], "OBJ.G2.V2" if row.point.kind == "BI" else "OBJ.G32.V7"]
                else:
                    capabilities = [f"OBJ.G60.V{row[0] + 1}"]
                parameters.append(pytest.param(row, id=f"class-{row[0]}" if name == "sim_class_count" else row.id,
                    marks=[pytest.mark.dnp3_capability(capability) for capability in capabilities]))
            metafunc.parametrize(name, parameters)


@pytest.fixture
def sim_master(dnp3_simulator_settings, dnp3_host_config):
    # Each test owns its host; setup verifies an actual point READ, not just TCP.
    with simulator_session(dnp3_simulator_settings, dnp3_host_config) as client:
        yield client
