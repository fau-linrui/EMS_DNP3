# Python 子进程客户端与 pytest 集成（0.4.0）

`dnp3_master` 核心只依赖 Python 标准库。它启动 `dnp3-master-host.exe`、自动完成 hello、串行化单个在途请求、持续排空 stdout/stderr、验证严格响应、处理超时/异常退出，并在 Windows Job Object 中拥有整个子进程树。

## 直接使用

```python
from pathlib import Path

from dnp3_master import (
    Dnp3MasterClient,
    HostProcessConfig,
    ReadHeader,
    TcpConnectionConfig,
)

process = HostProcessConfig(
    executable=Path("bin/dnp3-master-host.exe"),
    safety_incident_directory=Path("evidence/local/safety-incidents"),
    startup_timeout=5.0,
    request_timeout=10.0,
    shutdown_timeout=2.0,
)

with Dnp3MasterClient(process) as master:
    assert master.hello_info["backend"] == "opendnp3"
    master.connect(
        TcpConnectionConfig(
            host="192.0.2.10",
            port=20000,
            master_address=1,
            outstation_address=1024,
        )
    )

    integrity = master.integrity_poll(timeout=10.0)
    analog = master.read(
        [ReadHeader.range16(30, 0, 0, 9)],
        timeout=5.0,
    )
    assert integrity.task_status == "SUCCESS"
    assert all(item.kind == "analog_input" for item in analog.measurements)

    events = master.class_poll((1, 2, 3), return_mode="summary")
    stats = master.get_stats()
    master.disconnect()
```

`ReadHeader` 提供 `all_objects`、`range8/range16`、`count8/count16` 工厂。一次 `read` 接受 1～64 个 Header。结果为不可变 `ReadTaskResult`，含 `measurements`、`summary`、`fragments`、`iin`、`timings` 和原始映射；`measurements_of_kind()` 可按统一 kind 过滤。

`return_mode="summary"` 不在结果中保留或返回逐点对象，适合大响应；`max_measurements` 仍是完整性上限，超过时 host 返回 `QUEUE_OVERFLOW`。

## 主动上送 API

主动上送只由调用方显式控制：

```python
enabled = master.enable_unsolicited((1, 2), timeout=5.0)
batch = master.wait_unsolicited(wait_timeout=10.0, max_events=256)
master.disable_unsolicited((1, 2), timeout=5.0)
```

每条 `MeasurementRecord` 包含 `source="unsolicited"`、`session_id`、分片和接收顺序。持久队列默认上限 4096，满时 drop-oldest 并增加 `dropped_total`；调用方不能忽略丢弃计数。断开会结束收集并清队列。本机已覆盖启停、G2V2/G32V7、禁用后无新事件和队列溢出；Confirm 丢失、重发/重复、序号回绕等原始时序仍保持未验证。

## 控制 API

控制只有在连接时显式声明获批 LAB 会话才会解锁：

```python
from dnp3_master import (
    AnalogOutputCommand,
    CrobCommand,
    LabSafetyConfig,
    TcpConnectionConfig,
)

master.connect(
    TcpConnectionConfig(
        host="192.0.2.10",
        safety=LabSafetyConfig(
            operator_id="approved-operator-or-ticket",
            dut_id="lab-asset-id",
            allow_state_change=True,
        ),
    )
)

result = master.select_and_operate(
    [
        CrobCommand(index=0, operation="latch_on"),
        AnalogOutputCommand.int16(index=1, value=-123),
    ],
    timeout=10.0,
)

assert result.task_status == "SUCCESS"
assert result.all_success
assert all(point.status == "SUCCESS" for point in result.point_results)
```

还提供 `AnalogOutputCommand.int32/float32/double64` 和 `direct_operate()`。每点结果保留原始/解析 CommandPointState、CommandStatus 和请求关联。调用方不能只检查 `all_success` 而忽略每点状态。

Python 客户端只在内存保存 host 返回的一次性令牌，不提供公开 token 属性；高层 `connect()` 返回值会移除令牌并标记 `token_exposed=False`，诊断尾部也会过滤令牌。disconnect/close 后清除。令牌不是认证或 SAv5。

状态改变 API 还要求 `HostProcessConfig.safety_incident_directory`。控制 timeout、host 通信失败、非法控制结果或 `execution_uncertain=true` 后，客户端先按 DUT 哈希持久化事故锁，再清令牌并终止 host。新进程可继续只读，但控制会抛出 `UnresolvedSafetyIncidentError`；完成独立读回后用 `active_safety_incident()` 和 `acknowledge_safety_incident()` 显式归档。不能删除锁或自动重试，详见 `docs/SAFETY_INCIDENT_RUNBOOK.md`。Direct Operate 的 `response_mode="no_response"` 当前稳定返回 `UNSUPPORTED_BY_BACKEND`。

## 嵌入现有 pytest

目标框架根 `conftest.py`：

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

插件 fixtures：

| Fixture | Scope/内容 |
|---|---|
| `dnp3_pics` | session；已校验的 capability -> 三态映射 |
| `dnp3_point_table` | session；严格加载的只读点表，未配置时为 `None` |
| `dnp3_ems_test_plan` | session；与点表交叉校验的完整性/Class、主动上报和控制场景计划，未配置时为 `None` |
| `dnp3_host_config` | session；可覆盖的 `HostProcessConfig` |
| `host_process` | session；已完成 hello 的客户端，teardown 幂等清理 |
| `master_client` | session；`host_process` 的别名边界 |
| `dnp3_connection_config` | function；从 CLI/env 生成严格 TCP 配置，仅为已标记且获批的当前用例附加安全配置 |
| `connected_master` | function；每个测试连接并在 teardown 断开 |

pytest-xdist 每个 worker 会创建独立 host/session；若 EMS 只允许一个主站，DUT 用例必须串行，不能使用多个 worker。

## 严格 EMS 场景计划

`--dnp3-ems-plan` 或 `DNP3_EMS_PLAN` 加载 `config/ems_test_plan.example.json` 格式的私有副本。使用计划时必须同时提供点表；插件会在建立 DUT 连接前完成以下检查：

- JSON 最多 1 MiB，拒绝重复键、未知字段、非标准数字和重复场景 ID。
- 所有 point ID 必须存在且启用；Class/unsolicited 场景还必须与点表中的 Event Class、Group 和 Variation 一致。
- timeout、测量/事件上限和期望值均有界，布尔点只能使用布尔期望，数值点只能使用有限数值期望。
- 控制的操作和恢复必须使用相同命令类型/索引但载荷不同，恢复期望必须等于操作前基线。
- 已启用控制不能保留 `FILL_ME/TODO/TBD/PLACEHOLDER/EXAMPLE` 授权引用。

公开点表保持只读；控制值、反馈关系和授权只存在于未提交的私有场景计划中。证据清单只记录计划文件名、大小和 SHA-256，不复制内容。

## PICS 选择门

真实 DUT 用例必须使用：

```python
@pytest.mark.dnp3_dut
@pytest.mark.dnp3_capability("APP.FC.01.READ")
def test_real_ems_read(connected_master):
    ...
```

通过 `--dnp3-pics-file` 或 `DNP3_PICS_FILE` 传入 `config/ems_profile.example.json` 格式的本地副本。解析器拒绝重复键、未知根字段、非法设备字段、非法 ID/状态，以及能力矩阵中不存在的 ID，防止 PICS 和 marker 同时拼错后误运行。插件通常会自动找到同一源码包中的 `config/capability_matrix.csv`；目录布局不同可用 `--dnp3-capability-matrix` 或 `DNP3_CAPABILITY_MATRIX` 指定。

- `SUPPORTED`：正向用例运行。
- `NOT_SUPPORTED`：正向用例跳过；带 `dnp3_unsupported_behavior` 的负向用例才运行。
- `UNKNOWN`/缺失：按 `--dnp3-unknown-policy=xfail|skip|error` 处理，默认 xfail 且不运行测试体。

正式 EMS 执行推荐 `--dnp3-unknown-policy error`，避免漏填 PICS 被误认为通过。

## 状态改变收集门

可能修改 DUT 的 pytest 用例还必须带：

```python
@pytest.mark.dnp3_state_changing
```

默认收集后直接 skip。只有命令行同时加入下列参数才会运行，并让 `connected_master` 创建 `LabSafetyConfig`：

```powershell
--dnp3-allow-state-changing `
--dnp3-operator-id "<OPERATOR_OR_TICKET>" `
--dnp3-dut-id "<LAB_ASSET_ID>"
```

环境变量等价为 `DNP3_ALLOW_STATE_CHANGING`、`DNP3_OPERATOR_ID`、`DNP3_DUT_ID`。这只是技术防误触；项目书面授权、点表确认、回退方案和人工监护仍不可省略。

捆绑的 `examples/pytest_ems/test_control_scenarios.py` 还要求计划场景已启用，并在每次命令中用 `--dnp3-control-scenario <精确ID>` 单独选择。该参数故意没有环境变量替代项，一次只能选择一个场景。模板先读基线，只发送一次操作；只有明确成功且反馈确认后才发送一次预批准恢复，任何控制都不自动重试。插件拒绝已授权状态改变测试使用 pytest-xdist，模板拒绝同一 pytest 进程内的 rerun/repeat。

## 主要 pytest 参数/环境变量

| CLI | 环境变量 | 默认 |
|---|---|---|
| `--dnp3-host-exe` | `DNP3_MASTER_HOST_EXE` | 必填（使用 fixture 时） |
| `--dnp3-pics-file` | `DNP3_PICS_FILE` | 无 |
| `--dnp3-capability-matrix` | `DNP3_CAPABILITY_MATRIX` | 自动查找 `config/capability_matrix.csv` |
| `--dnp3-points-file` | `DNP3_POINTS_FILE` | 无；提供时在收集前严格校验 |
| `--dnp3-ems-plan` | `DNP3_EMS_PLAN` | 无；使用时必须同时提供点表 |
| `--dnp3-control-scenario` | 无 | 无；每次精确选择一个已启用控制场景 |
| `--dnp3-evidence-dir` | `DNP3_EVIDENCE_DIR` | 无；提供时生成脱敏运行清单 |
| `--dnp3-safety-incident-dir` | `DNP3_SAFETY_INCIDENT_DIR` | `evidence/local/safety-incidents` |
| `--dnp3-unknown-policy` | `DNP3_UNKNOWN_POLICY` | `xfail` |
| `--dnp3-outstation-host` | `DNP3_OUTSTATION_HOST` | 使用 connected fixture 时必填 |
| `--dnp3-outstation-port` | `DNP3_OUTSTATION_PORT` | 20000 |
| `--dnp3-local-adapter` | `DNP3_LOCAL_ADAPTER` | 0.0.0.0 |
| `--dnp3-master-address` | `DNP3_MASTER_ADDRESS` | 1 |
| `--dnp3-outstation-address` | `DNP3_OUTSTATION_ADDRESS` | 1024 |
| `--dnp3-connect-timeout` | `DNP3_CONNECT_TIMEOUT` | 5 s |
| `--dnp3-retry-min/max` | `DNP3_RETRY_MIN/MAX` | 1/60 s |
| `--dnp3-keep-alive-timeout` | `DNP3_KEEP_ALIVE_TIMEOUT` | 60 s |

host 启动/请求/关闭 timeout 也可通过 `--dnp3-startup-timeout`、`--dnp3-request-timeout`、`--dnp3-shutdown-timeout` 设置。

## 错误与诊断

| 异常 | 含义 |
|---|---|
| `HostStartError` | EXE、工作目录、管道或 Job Object 初始化失败 |
| `HostTimeoutError` | hello/请求超时；客户端终止 host，禁止复用未知会话 |
| `HostExitedError` | host 在预期响应前退出 |
| `HostProtocolError` | stdout 非协议文本、非法 UTF-8/JSON、重复键、错误 Schema/ID/结果类型 |
| `HostCommandError` | host 返回合法错误；读取 `.code` 和 `.details` |
| `ClientStateError` | 对未启动、失败、关闭或未安全解锁的客户端操作 |
| `SafetyIncidentConfigurationError` | 状态改变 API 没有可持久化的事故目录/DUT 身份 |
| `UnresolvedSafetyIncidentError` | 同一 DUT 有未确认的不确定控制结果，控制在发包前被阻止 |
| `SafetyIncidentAcknowledgmentError` | 事故 ID、确认字段或独立读回数据不完整/不匹配 |
| `SafetyIncidentPersistenceError` | 事故锁无法可靠写入、读取、归档；必须人工停止控制 |

`client.diagnostics` 提供 PID、退出码、stdout/stderr 有界尾部、Job Object 状态和清理错误。默认每流最多保留最后 64 KiB，响应队列有界；正常 stdout 协议响应不会进入诊断，只有畸形 stdout 会保留有界尾部，且令牌字段会再次过滤。

## 关闭与生命周期

上下文退出时先发送 `shutdown`；若 host 无响应，关闭 Job Object 并强制回收进程树。`close()` 可重复调用，返回最终 `HostProcessDiagnostics`。超时/协议破坏后 client 进入不可复用状态，应新建实例。

正式资源回收验收：

```powershell
.\scripts\test-lifecycle.ps1 -Preset windows-msvc-release -Iterations 1000
```

## 可复制边界

推荐使用 `scripts/package.ps1` 的整个产物。最小运行边界是：

```text
python/src/dnp3_master/
bin/dnp3-master-host.exe
bin/build-info.json
schemas/
config/capability_matrix.csv
config/ems_profile.example.json
config/points.example.csv
config/ems_test_plan.example.json
examples/pytest_ems/
package-manifest.json
```

完整迁移、构建、首次 EMS 连接和排错步骤见 `docs/BEGINNER_MIGRATION_BUILD_USE_GUIDE.md`。
