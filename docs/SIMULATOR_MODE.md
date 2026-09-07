# 模拟器环境：免审批遥控、遥调与批量 pytest

适用于“EMS 连接的设备全部是模拟器，允许任意下发控制”的测试环境。
模拟器模式仍通过 TCP 发送真正的 DNP3 报文，不是 mock 或只打印命令。
程序不根据 IP 判断设备真假；环境性质由使用者配置。

## 1. 配置一次即可

已有 pytest 框架的根 `conftest.py` 启用插件（已有则不重复添加）：

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

在已有 `pytest.ini` 的 `[pytest]` 节增加一行，不要覆盖原配置：

```ini
[pytest]
dnp3_simulator = true
```

若使用 `pyproject.toml`，在 `[tool.pytest.ini_options]` 下增加
`dnp3_simulator = true`。也可临时传 `--dnp3-simulator`，或设置环境变量
`DNP3_SIMULATOR=1`。优先级：命令行启用 > 环境变量 > ini；环境变量支持
`1/0`、`true/false`、`yes/no`、`on/off`，其他值报配置错误。
未启用时保持原 LAB/只读默认行为。
模式在 pytest 配置阶段确定，本次运行不受用例中途修改环境变量影响。

开启后，不再需要：

- `--dnp3-allow-state-changing`、operator ID、DUT ID 或工单。
- 人工传递 safety token（内部会话令牌由 Python 自动管理）。
- 事故目录、人工读回解锁、持久事故确认。
- 每次用 `--dnp3-control-scenario` 精确点名一个场景。
- 正式 PICS 才能执行已实现功能。UNKNOWN/未提供 PICS 不阻止模拟器用例；若明确提供
  `NOT_SUPPORTED`，正向用例仍按不适用跳过；框架未实现的能力仍不执行。

保留类型/范围、协议结果关联、超时和队列上限、命令状态及读回断言。
FC6 No Response 等未实现能力仍返回 `UNSUPPORTED_BY_BACKEND`。

## 2. 运行现成的遥控、遥调场景

先用本次代码重新构建 host 并更新 Python 包；旧 host 不认识 SIMULATOR：

```powershell
.\scripts\build.ps1 -Preset windows-msvc-release
.\.venv\Scripts\python.exe -m pip install -e ".\python[test]"
```

源码仓库根目录准备本地配置（目标文件已有时不要覆盖）：

```powershell
Copy-Item .\config\points.example.csv .\config\points.local.csv
Copy-Item .\config\ems_test_plan.simulator.example.json .\config\ems_test_plan.local.json
```

修改点表索引、反馈点和期望值以匹配模拟器。新模板已启用两个 FC5 场景：索引 0 的
CROB LATCH_ON，以及索引 0 的 Float32 遥调到 1.0。示例数值不是你的 EMS 点表。

以下地址是文档示例，执行前替换为模拟 EMS 从站地址，不是主站本机 IP：

```powershell
$env:DNP3_MASTER_HOST_EXE = "$PWD\out\build\windows-msvc-release\bin\dnp3-master-host.exe"
$env:DNP3_OUTSTATION_HOST = "192.0.2.10"
$env:DNP3_OUTSTATION_PORT = "20000"
.\.venv\Scripts\python.exe -m pytest .\examples\pytest_ems\test_control_scenarios.py -q `
  --dnp3-simulator `
  --dnp3-points-file .\config\points.local.csv `
  --dnp3-ems-plan .\config\ems_test_plan.local.json
```

ini 已启用时省略 `--dnp3-simulator`。未选择 ID 时执行所有 `enabled=true` 的控制场景；
可用 `-k` 或原有 `--dnp3-control-scenario <ID>` 筛选。重复执行和 repeat/rerun 插件不再
被模板拦截；框架本身仍不隐藏重试命令。

计划根节点写 `"environment": "SIMULATOR"` 才允许省略审批/恢复字段；这个字段
不自动打开连接模式，pytest 配置也需启用模拟器模式。原 LAB 计划可在模拟器连接
上运行，但其已有前置检查/恢复动作仍会执行。

SIMULATOR 计划中：

- `authorization_reference`、`precondition` 可不填；不是填写空字符串或 null。
- `postcondition` 必填：控制之后读到什么才算通过。
- `restore_command`、`restore_expectation` 可一起省略；如提供则成对提供并执行校验。
  无需强制恢复成初值，也允许重复写同一个值。
- 不恢复时，模拟器保留控制后的状态，下一条用例应按此设计。

测试路径改为 `examples\pytest_ems` 可同时运行逐点读取和已配置的 Poll/主动上报场景。
主动上报仍需模拟器/外部信号接口制造变化；打开模式不会自动制造事件。

## 3. 自己写 pytest 用例

使用 `connected_master`；启动、连接、令牌和关闭由 fixture 处理：

```python
import pytest
from dnp3_master import AnalogOutputCommand, CrobCommand


@pytest.mark.dnp3_dut
@pytest.mark.dnp3_state_changing
@pytest.mark.dnp3_capability("APP.FC.05.DIRECT_OPERATE")
@pytest.mark.dnp3_capability("APP.COMMAND_STATUS.CATALOG")
@pytest.mark.dnp3_capability("OBJ.G12.V1")
@pytest.mark.dnp3_capability("OBJ.G41.V3")
@pytest.mark.dnp3_capability("QUAL.Q17.REVIEW")
def test_simulator_outputs(connected_master):
    assert connected_master.direct_operate([CrobCommand(0, "latch_on")]).all_success
    result = connected_master.direct_operate([
        AnalogOutputCommand(0, 1.0, "analog_output_float32"),
    ])
    assert result.all_success
```

这个短用例只检查 DNP3 命令应答；确认 EMS 反馈值请用上一节场景模板。
自写用例不要求加载场景计划。不通过 pytest 调用时，显式设置连接模型。
下例从可移植包根目录运行（源码仓库应替换 EXE 路径）：

```python
from pathlib import Path
from dnp3_master import Dnp3MasterClient, HostProcessConfig, TcpConnectionConfig, CrobCommand

with Dnp3MasterClient(HostProcessConfig(Path("bin/dnp3-master-host.exe"))) as client:
    client.connect(TcpConnectionConfig(host="192.0.2.10", simulator=True))
    assert client.direct_operate([CrobCommand(0, "latch_on")]).all_success
```

直接 API 不读取 pytest 的环境开关。无需 `LabSafetyConfig` 或事故目录，且不能同时
设置 `simulator=True` 和 LAB safety。`client.simulator_mode` 标识最近一次成功连接的
模式；`state_change_authorized` 才表示当前仍有可用控制令牌。

## 4. 失败与移植边界

控制超时/结果不确定仍抛异常并关闭当前 host（该会话的任务状态已不可靠），但不读、
不写、不清除 LAB 事故锁，也无需事故确认。异常 details 标记 `environment=SIMULATOR`、
`persistent_safety_lock=false`、`required_action=START_NEW_SESSION`。
直接 API 创建新 client 即可继续；不可重新 `start()` 已关闭的 client。

模拟器 `connected_master` 为每条测试创建自己的 host，本条失败不会永久锁住下一条。
强制回收保留 cleanup 诊断，pytest 可能同时报告 call failure 和 teardown error；
两者都不是事故锁。模拟器 fixture 不复用 session 级 `master_client`，需要定制程序
路径/参数时覆盖 `dnp3_host_config`。

不禁止 xdist，但同一点并行写入会互相覆盖、使断言失真；批量通常使用 pytest 默认
串行就够了。模式没有改变“单 client 单在途请求”的边界。

移植仍复制可移植包，或同时更新 Python 包、新 EXE/build-info、能力矩阵、
`examples/pytest_ems` 和配置/Schema。不要只复制旧 EXE 或只改 pytest 参数。
详见 [小白移植指南](BEGINNER_MIGRATION_BUILD_USE_GUIDE.md)。

本次仍属 0.6.1 后的未发布变更。如果目标环境已安装同版本的旧 wheel，更新时使用
`pip install --no-index --no-deps --force-reinstall <新包内的wheel路径>`，避免 pip 因
版本号相同而跳过安装；源代码 editable 安装按第 2 节更新即可。

`preflight` 的正式身份/PICS 准备检查和 [事故手册](SAFETY_INCIDENT_RUNBOOK.md) 面向 LAB，
不是模拟器运行前置条件。点表/计划仍在 pytest 配置阶段严格校验。
pytest 证据包含 `runner.dnp3_environment=SIMULATOR`，不得当作真实设备验收。

已用 fake host 验证失败隔离/中断，用本机 OpenDNP3 从站验证批量、重复 CROB/Float32 和
反馈断言；不等于已经联调你的 EMS 模拟器。
