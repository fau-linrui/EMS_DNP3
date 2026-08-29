# Python 子进程客户端与 pytest 集成（0.2.0）

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

Python 客户端只在内存保存 host 返回的一次性令牌，不提供公开 token 属性；高层 `connect()` 返回值会移除令牌并标记 `token_exposed=False`，诊断尾部也会过滤令牌。disconnect/close 后清除。令牌不是认证或 SAv5。控制 timeout 后 host 可能仍执行，客户端将会话视为未知状态并终止进程；业务层不得自动重试。Direct Operate 的 `response_mode="no_response"` 当前稳定返回 `UNSUPPORTED_BY_BACKEND`。

## 嵌入现有 pytest

目标框架根 `conftest.py`：

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

插件 fixtures：

| Fixture | Scope/内容 |
|---|---|
| `dnp3_pics` | session；已校验的 capability -> 三态映射 |
| `dnp3_host_config` | session；可覆盖的 `HostProcessConfig` |
| `host_process` | session；已完成 hello 的客户端，teardown 幂等清理 |
| `master_client` | session；`host_process` 的别名边界 |
| `dnp3_connection_config` | function；从 CLI/env 生成严格 TCP 配置，仅为已标记且获批的当前用例附加安全配置 |
| `connected_master` | function；每个测试连接并在 teardown 断开 |

pytest-xdist 每个 worker 会创建独立 host/session；若 EMS 只允许一个主站，DUT 用例必须串行，不能使用多个 worker。

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

## 主要 pytest 参数/环境变量

| CLI | 环境变量 | 默认 |
|---|---|---|
| `--dnp3-host-exe` | `DNP3_MASTER_HOST_EXE` | 必填（使用 fixture 时） |
| `--dnp3-pics-file` | `DNP3_PICS_FILE` | 无 |
| `--dnp3-capability-matrix` | `DNP3_CAPABILITY_MATRIX` | 自动查找 `config/capability_matrix.csv` |
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
```

完整迁移、构建、首次 EMS 连接和排错步骤见 `docs/BEGINNER_MIGRATION_BUILD_USE_GUIDE.md`。
