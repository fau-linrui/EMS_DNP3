# Python 子进程客户端与 pytest 集成

`dnp3_master` 包只依赖 Python 标准库。它负责启动 `dnp3-master-host.exe`、自动完成 `hello`、串行化单个在途 NDJSON 请求、持续排空 stdout/stderr、处理超时和异常退出，并在 Windows Job Object 中拥有整个子进程树。T05 增加了严格校验的 TCP 连接模型与状态事件 API。

## 直接使用

```python
from pathlib import Path

from dnp3_master import Dnp3MasterClient, HostProcessConfig, TcpConnectionConfig

config = HostProcessConfig(
    executable=Path("bin/dnp3-master-host.exe"),
    startup_timeout=5.0,
    request_timeout=10.0,
    shutdown_timeout=2.0,
)

with Dnp3MasterClient(config) as master:
    assert master.hello_info["backend"] == "opendnp3"
    master.connect(TcpConnectionConfig(host="192.0.2.10"))
    events = master.wait_event(wait_timeout=1.0)
    assert master.get_status()["state"] == "CONNECTED"
    master.disconnect()
```

进入上下文时会启动进程并在 `startup_timeout` 内完成 `hello`。退出时先请求 `shutdown`；如 host 无响应，则关闭 Job Object 并强制回收整个进程树。`close()` 可以重复调用，结果一致且不会遗留子进程。

## 嵌入现有 pytest 框架

在目标框架的根 `conftest.py` 中启用插件：

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

然后通过环境变量指定 EXE：

```powershell
$env:DNP3_MASTER_HOST_EXE = 'D:\tools\dnp3\dnp3-master-host.exe'
pytest
```

也可以使用 `--dnp3-host-exe`。插件提供以下 session fixture：

| Fixture | 内容 |
|---|---|
| `dnp3_host_config` | 可在项目 `conftest.py` 中覆盖的 `HostProcessConfig`。 |
| `host_process` | 已完成 `hello` 的进程客户端，teardown 始终执行幂等清理。 |
| `master_client` | 当前与 `host_process` 指向同一客户端。 |
| `dnp3_connection_config` | 从命令行/环境变量生成的 `TcpConnectionConfig`；可在既有框架中覆盖。 |
| `connected_master` | function-scope 已连接客户端；teardown 按状态幂等断开。 |

`connected_master` 至少需要 `DNP3_OUTSTATION_HOST` 或 `--dnp3-outstation-host`。端口默认 20000；还可配置 `DNP3_OUTSTATION_PORT`、`DNP3_MASTER_ADDRESS`、`DNP3_OUTSTATION_ADDRESS`、`DNP3_CONNECT_TIMEOUT`、`DNP3_RETRY_MIN`、`DNP3_RETRY_MAX` 和 `DNP3_KEEP_ALIVE_TIMEOUT`。pytest-xdist 的 session fixture 按 worker 隔离；若 EMS 只允许一个主站连接，真实连接用例必须串行。

## 错误与诊断

| 异常 | 含义 |
|---|---|
| `HostStartError` | EXE、工作目录或 Windows Job Object 初始化失败。 |
| `HostTimeoutError` | `hello` 或单个在途请求超时；客户端立即终止 host，禁止继续复用未知状态的会话。 |
| `HostExitedError` | host 在返回预期响应前异常退出。 |
| `HostProtocolError` | stdout 出现非协议文本、非法 UTF-8/JSON、重复键、错误 schema 或错配 ID。 |
| `HostCommandError` | host 返回合法错误信封；通过 `code` 和 `details` 判断，不依赖 message。 |
| `ClientStateError` | 对未启动、已失败或已关闭客户端提交请求。 |

`client.diagnostics` 提供 PID、退出码、stdout/stderr 有界尾部、Job Object 分配状态、清理错误和预留的 dump 路径。默认每个输出流只保留最后 64 KiB，待处理 stdout 响应队列也固定有上限；日志或非请求响应无法无限占用 Python 内存。环境变量和命令行未写入诊断，避免意外记录敏感配置。

## 可复制目录

集成到其他 pytest 项目时复制：

```text
python/src/dnp3_master/
schemas/
bin/dnp3-master-host.exe
```

将 `python/src` 加入目标项目的包搜索路径或把该包安装到测试虚拟环境。核心客户端不导入 OpenDNP3 类型，后端替换不会改变 pytest 的进程管理代码。

## 生命周期验收

默认测试套件只收集并跳过长循环。正式验收使用：

```powershell
.\scripts\test-lifecycle.ps1 -Preset windows-msvc-release -Iterations 1000
```

测试逐次完成启动、`hello` 和 `shutdown`，等待退出码，确认读取线程归零，并比较当前 Python 进程在循环前后的 Windows handle 数量。
