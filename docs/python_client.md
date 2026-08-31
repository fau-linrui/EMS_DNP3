# Python 子进程客户端与 pytest 集成（0.6.0）

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


_REQUEST_ERROR_IIN_BITS = frozenset(
    {
        "IIN2.0.NO_FUNC_CODE_SUPPORT",
        "IIN2.1.OBJECT_UNKNOWN",
        "IIN2.2.PARAMETER_ERROR",
    }
)


def assert_read_accepted(result):
    assert result.task_status == "SUCCESS"
    assert _REQUEST_ERROR_IIN_BITS.isdisjoint(result.iin["bits"])
    assert result.iin["observation_window_dropped"] == 0


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
    assert_read_accepted(integrity)
    assert_read_accepted(analog)
    assert all(item.kind == "analog_input" for item in analog.measurements)

    events = master.class_poll((1, 2, 3), return_mode="summary")
    assert_read_accepted(events)
    stats = master.get_stats()
    master.disconnect()
```

直接调用客户端不会自动加载私有 PICS，也不会像 pytest marker 一样做 DUT/框架能力选择；调用方必须先完成离线预检，并保证所请求功能码、对象和 Qualifier 都已由目标设备声明支持。真实 EMS 用例优先使用下文 pytest 插件和捆绑场景模板。

启动握手会校验 host 版本；pytest 插件还会计算本次 `capability_matrix.csv` 的 SHA-256，并要求 host 内嵌哈希完全一致，防止复制/升级时混用 Python、host 和能力矩阵。`request()` 只保留给尚无类型化 API 的扩展命令；`connect/read/control/disconnect/shutdown` 等已实现命令禁止原始调用，必须走对应高层方法，避免绕过会话状态、结果校验或安全事故锁。

`ReadHeader` 提供 `all_objects`、`range8/range16`、`count8/count16` 工厂。一次 `read` 接受 1～64 个 Header。结果为不可变 `ReadTaskResult`，含 `measurements`、`summary`、`fragments`、`iin`、`timings` 和原始映射；`measurements_of_kind()` 可按统一 kind 过滤。

`return_mode="summary"` 不在结果中保留或返回逐点对象，适合大响应；`max_measurements` 仍是完整性上限。测量溢出、超过 4096 个分片，或当前任务窗口内超过 1024 条 IIN 观测导致丢失时，host 都返回 `QUEUE_OVERFLOW`。Python 还会校验 summary/IIN 计数、原始字节和存储上限的一致性。

OpenDNP3 task `SUCCESS` 不等于对象请求成功。真实 EMS 场景会额外拒绝 IIN2.0 `NO_FUNC_CODE_SUPPORT`、IIN2.1 `OBJECT_UNKNOWN` 和 IIN2.2 `PARAMETER_ERROR`，所以配置为“允许空响应”的 Class 场景也不会把错误响应当作通过。

## 主动上送 API

主动上送只由调用方显式控制：

```python
enabled = master.enable_unsolicited((1, 2), timeout=5.0)
try:
    assert enabled.task_status == "SUCCESS"
    batch = master.wait_unsolicited(wait_timeout=10.0, max_events=256)
    assert batch.summary["dropped_total"] == 0
finally:
    disabled = master.disable_unsolicited((1, 2), timeout=5.0)
    assert disabled.task_status == "SUCCESS"
```

每条 `MeasurementRecord` 包含 `source="unsolicited"`、`session_id`、分片和接收顺序。持久队列默认上限 4096，满时 drop-oldest 并增加 `dropped_total`；调用方不能忽略丢弃计数。断开会结束收集并清队列。本机已覆盖启停、G2V2/G32V7、禁用后无新事件和队列溢出；Confirm 丢失、重发/重复、序号回绕等原始时序仍保持未验证。

## 持续 Capture API

`begin_capture(CaptureConfig)`、`capture_progress(capture_id)` 和
`end_capture(capture_id)` 提供跨 Read/unsolicited 回调的持续有界汇总。一个
会话最多一个 ACTIVE capture；断开、shutdown、deadline、drain timeout 和
queue overflow 都会得到明确无效终态。静态点集模式检查 missing/duplicate/
unmatched，事件模式用外部 manifest 的总数与有序 SHA-256 对账，observation
模式不声称完整性。完整示例和 canonical event 规则见
`docs/PERFORMANCE_AND_SOAK_GUIDE.md`。

`end_capture()` 遇到队列溢出会抛 `HostCommandError(code="QUEUE_OVERFLOW")`，
但不会丢失诊断；严格终态位于 `error.details["operation_result"]`。调用方不得
捕获后继续把该轮标为通过。

## 性能与 soak API

`load_performance_profile()` 对 Profile 做严格字段/边界/基线检查并绑定源文件
SHA-256。每个场景必须声明总对象数、按 kind 和按 `group:variation` 的精确每轮
分布；`run_performance_suite()` 会逐轮核对，输出 capture A/B 开销、最近秩
p50/p95/p99/max 和 Windows host 资源阈值结果。

`load_local_event_profile()` 和 `run_local_event_benchmark()` 只服务于包内回环
从站。底层发生器最多能表达 65,535 条请求，但严格 Profile/Schema 把每个负载块
限制为 4,096 条，并要求不超过从站事件缓冲。runner 会分别核验 native capture
的总数/有序 SHA-256/overflow，以及 master unsolicited 队列的排空数和
`dropped_total`；任一证据路径不完整，报告都不会通过。

`run_soak()` 只循环只读 Read/Integrity/Class Poll，使用 watchdog、固定容量
样本、连接次数、磁盘余量、证据字节上限、原子检查点、哈希链和轮转。它还会
消费 native 的 1,024 条有界 channel-event 队列，捕获状态快照之间的短暂断线；
任何 drop 都使重连历史不可证明并 fail closed。Profile 加载时要求 watchdog
严格大于单场景 begin、Read、end/drain 的全部有界 RPC 预算。
`write_json_report()` 原子保存普通性能/事件报告并默认拒绝覆盖；返回 path、
size 和 SHA-256。可直接复制 `examples/pytest_performance`，24 小时用例还要求
显式 `--dnp3-run-soak`。

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

还提供 `AnalogOutputCommand.int32/float32/double64` 和 `direct_operate()`。每点结果保留 CommandPointState、结构化 CommandStatus 和请求关联。Python 会验证 mode、批次数、summary 计数、header/index 唯一性以及每个 request ordinal/type/index 与原请求完全对应。`point.status` 是基于 OpenDNP3 解码值生成的 IEEE 1815-2012 Table 11-7 视图；`status_raw` 是后端解码枚举值，`status_edition` 固定为 `IEEE1815-2012`，后端后续版本名称只通过 `status_backend` 暴露。固定栈可区分 13～18 并将其规范化为 2012 `RESERVED`，但会把未识别的线上 19～125 折叠成 127；此时 `status_wire_raw_unambiguous` 为 false，不能断言线上原值一定是 `UNDEFINED`。

Python 客户端只在内存保存 host 返回的一次性令牌，不提供公开 token 属性；高层 `connect()` 返回值会移除令牌并标记 `token_exposed=False`，诊断尾部也会过滤令牌。disconnect/close 后清除。令牌不是认证或 SAv5。

状态改变 API 还要求 `HostProcessConfig.safety_incident_directory`。控制 timeout、host 通信失败、非法/错配控制结果、`execution_uncertain=true`，或点级 `TIMEOUT`、2012 保留状态、raw 127 歧义出现后，客户端先按 DUT 哈希持久化事故锁，再清令牌并终止 host。新进程可继续只读，但控制会抛出 `UnresolvedSafetyIncidentError`；完成独立读回后用 `active_safety_incident()` 和 `acknowledge_safety_incident()` 显式归档。不能删除锁或自动重试，详见 `docs/SAFETY_INCIDENT_RUNBOOK.md`。Direct Operate 的 `response_mode="no_response"` 当前稳定返回 `UNSUPPORTED_BY_BACKEND`。

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
| `dnp3_performance_profile` | session；严格且已绑定 SHA-256 的性能/soak Profile，未配置时为 `None` |
| `dnp3_local_event_profile` | session；仅本机从站使用的确定性事件负载 Profile，未配置时为 `None` |
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
- PICS 的 `SUPPORTED` 不能覆盖框架缺口：每个 marker 还必须在能力矩阵中处于 `IMPLEMENTED_UNVERIFIED` 或 `VERIFIED_*`；否则收集阶段会在接触 DUT 前跳过。
- 场景依赖包含实际请求限定符：逐点 range8/range16 为 Q00/Q01，Class/Integrity 和 unsolicited 控制为 Q06，控制索引前缀为 Q17/Q28。Q02/Q09/Q39 在固定后端中明确不可用。
- timeout、测量/事件上限和期望值均有界，布尔点只能使用布尔期望，数值点只能使用有限数值期望。
- 控制的操作和恢复必须使用相同命令类型/索引但载荷不同，恢复期望必须等于操作前基线。
- 已启用控制不能保留 `FILL_ME/TODO/TBD/PLACEHOLDER/EXAMPLE` 授权引用。

公开点表保持只读；控制值、反馈关系和授权只存在于未提交的私有场景计划中。证据清单只记录计划文件名、大小和 SHA-256，不复制内容。

## PICS 选择门

真实 DUT 用例必须使用：

```python
@pytest.mark.dnp3_dut
@pytest.mark.dnp3_capability("APP.FC.01.READ")
@pytest.mark.dnp3_capability("OBJ.G30.V5")
@pytest.mark.dnp3_capability("QUAL.Q01.REVIEW")
def test_real_ems_analog_range_read(connected_master):
    ...
```

每个 marker 只能接收一个能力 ID，手写用例必须重复声明实际功能码、对象和 Qualifier；上例代表 G30V5 的 16-bit range 读取。完整性扫描还需要 G60V1～V4、`APP.CLASS.EVENTS` 和 Q06。推荐使用 `examples/pytest_ems`，由点表/计划自动推导依赖，避免漏标。

通过 `--dnp3-pics-file` 或 `DNP3_PICS_FILE` 传入 `config/ems_profile.example.json` 格式的本地副本。解析器拒绝重复键、未知根字段、非法设备字段、非法 ID/状态，以及能力矩阵中不存在的 ID，防止 PICS 和 marker 同时拼错后误运行。插件通常会自动找到同一源码包中的 `config/capability_matrix.csv`；目录布局不同可用 `--dnp3-capability-matrix` 或 `DNP3_CAPABILITY_MATRIX` 指定。

- `SUPPORTED`：正向用例运行。
- `NOT_SUPPORTED`：正向用例跳过；带 `dnp3_unsupported_behavior` 的负向用例才运行。
- `UNKNOWN`/缺失：按 `--dnp3-unknown-policy=xfail|skip|error` 处理，默认 xfail 且不运行测试体。

正式 EMS 执行推荐 `--dnp3-unknown-policy error`，避免漏填 PICS 被误认为通过。

## DUT 连接前离线预检

pytest 插件与独立预检共用 `EmsProfile`/`load_ems_profile()` 的严格 PICS 模型。准备好私有 PICS、点表和场景计划后，先执行：

```powershell
python -m dnp3_master.preflight `
  --pics .\config\ems.local.json `
  --points .\config\points.local.csv `
  --plan .\config\ems_test_plan.local.json `
  --capability-matrix .\config\capability_matrix.csv
```

它不接受或读取 DUT IP/端口，不启动 host。退出码 0/2/3 分别表示离线 `READY`、配置无效、配置有效但存在 blocker。`--json` 报告含逐能力 PICS/框架判定和输入 SHA-256；完整规则见 `docs/OFFLINE_PREFLIGHT.md`。

本机回归需要制造事件或检查命令是否重发时，可使用 `dnp3_master.local_outstation.LocalTestOutstation` 控制包内回环从站。该帮助类不是面向真实 DUT 的接口，详见 `docs/LOCAL_TEST_OUTSTATION.md`。

## 状态改变收集门

可能修改 DUT 的 pytest 用例还必须带：

```text
@pytest.mark.dnp3_state_changing
```

默认收集后直接 skip。只有命令行同时加入下列参数才会运行，并让 `connected_master` 创建 `LabSafetyConfig`：

```text
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
| `--dnp3-performance-profile` | `DNP3_PERFORMANCE_PROFILE` | 无；严格加载并把 SHA-256 写入证据清单 |
| `--dnp3-local-event-profile` | `DNP3_LOCAL_EVENT_PROFILE` | 无；仅本机确定性事件发生器 |
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
config/performance_profile.example.json
config/local_event_profile.example.json
examples/pytest_ems/
examples/pytest_performance/
package-manifest.json
```

完整迁移、构建、首次 EMS 连接和排错步骤见
`docs/BEGINNER_MIGRATION_BUILD_USE_GUIDE.md`；性能与 24 小时步骤见
`docs/PERFORMANCE_AND_SOAK_GUIDE.md`。
