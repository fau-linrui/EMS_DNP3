# 本机可编程 DNP3 测试从站

`dnp3-local-test-outstation.exe` 是本项目随包交付的、只监听 `127.0.0.1` 的测试工具。它用于在没有真实 EMS 时验证完整调用链：

```text
pytest -> Python client -> dnp3-master-host.exe -> OpenDNP3 TCP
       -> dnp3-local-test-outstation.exe
```

它不是 EMS 模拟器、独立互操作端或一致性测试器。主站和测试从站都固定使用 OpenDNP3 3.1.2，所以通过结果只能证明本工程本机回归成功，能力状态仍是 `IMPLEMENTED_UNVERIFIED`。

## 已提供的本机点

默认提供索引 0 和 1。启动时可用 `point_count=1..65535` 扩展 BI、AI、BOS、
AOS 四类主点，并用 `event_buffer_capacity=1..65535` 配置事件缓冲；DBBI、
Counter、Frozen Counter、Octet String 和 Time-and-Interval 辅助对象仍只保留
前两个索引，避免大点表测试无意义地扩大九类数据库。

| 数据 | Static | Event/Class | 初始索引 0 值 |
|---|---|---|---|
| 遥信 BI | G1V2 | G2V2 / Class 1 | `false` |
| 遥测 AI | G30V5 | G32V7 / Class 2 | `123.5` |
| 遥控状态 BOS | G10V2 | 无 | `false` |
| 遥调状态 AOS | G40V3 | 无 | `0.0` |

除索引 0 的兼容初值外，主点按索引确定性初始化：BI/BOS 为索引奇偶值，AI 为
`index + 0.5`，AOS 为 `index`。因此任意获准的 `point_count` 都可复算；自动化
边界回归目前实际启动每类 4,096 点，共 16,384 点。

收到 G12V1 CROB 后，测试从站会更新对应 G10V2 状态；收到 G41V1～V4 后，会更新对应 G40V3 状态。这样可以离线验证“控制前读回、单次控制、控制后读回、单次恢复、恢复后读回”，而不接触真实设备。

## 推荐用法

源码仓库中先完成 Release 构建，然后运行：

```powershell
.\scripts\run-local-self-test.ps1 -Preset windows-msvc-release
```

如需只运行完整的 EMS native 场景测试，必须显式告诉测试代码两个已构建 EXE 的位置；`run-local-self-test.ps1` 会在结束时恢复进程环境，不能依赖它为下一条命令保留变量：

```powershell
$previousHostExe = $env:DNP3_MASTER_HOST_EXE
$previousOutstationExe = $env:DNP3_TEST_OUTSTATION_EXE
$env:DNP3_MASTER_HOST_EXE = (
  Resolve-Path '.\out\build\windows-msvc-release\bin\dnp3-master-host.exe'
).Path
$env:DNP3_TEST_OUTSTATION_EXE = (
  Resolve-Path '.\out\build\windows-msvc-release\bin\dnp3-local-test-outstation.exe'
).Path

try {
  .\.venv\Scripts\python.exe -m pytest -q `
    .\python\tests\test_ems_native_scenarios.py
}
finally {
  $env:DNP3_MASTER_HOST_EXE = $previousHostExe
  $env:DNP3_TEST_OUTSTATION_EXE = $previousOutstationExe
}
```

第一条是快速安装/调用链自检；第二条通过真实 native host 跑静态读取、Class Poll、主动上报和控制反馈恢复场景。四个稳定测试 ID 是：

- `TC_EMS_NATIVE_STATIC_AND_POLL_LOCAL_001`
- `TC_EMS_NATIVE_UNSOLICITED_LOCAL_001`
- `TC_EMS_NATIVE_CONTROL_CYCLE_LOCAL_001`
- `TC_LOCAL_OUTSTATION_CONTROL_PROTOCOL_001`

可移植包中执行 `self-test.ps1` 即可完成同样的快速回环检查。该自检还会对
BI/AI/BOS/AOS 索引 0～1 共 8 点执行 `static_set` capture；成功 JSON 中应有
`capture_expected=8`、`capture_received_unique=8` 和 `capture_valid=true`。

## Python 控制接口

需要为自定义本机 pytest 制造输入变化时，使用 `dnp3_master.local_outstation.LocalTestOutstation`，不要自行拼接它的 stdin JSON：

```python
from pathlib import Path
from dnp3_master.local_outstation import LocalTestOutstation

with LocalTestOutstation(
    Path("tools/dnp3-local-test-outstation.exe"),
    point_count=1024,
    event_buffer_capacity=8192,
) as outstation:
    outstation.update_binary_input(True, index=0, event_mode="force")
    outstation.update_analog_input(456.25, index=0, event_mode="force")
    state = outstation.snapshot()
    assert state["binary_output_status"][0] is False
```

`event_mode` 只接受：

- `detect`：值变化时按数据库规则生成事件；
- `force`：强制生成事件；
- `suppress`：只改静态值，不生成事件；
- `event_only`：只生成事件，不改静态值。

BI/AI 更新必须携带 DNP3 48 位毫秒时间；Python 帮助类默认使用当前时间。
更新索引必须小于启动时的 `point_count`，模拟量拒绝 NaN/Infinity。snapshot
只返回前 16 个点的有界样本，并通过 `snapshot_truncated` 表示截断，不能把它
误当成完整大点表导出。底层控制请求最大 64 KiB，响应有界，未知字段、重复
JSON 键、错误类型或未知命令均会失败。

## 确定性事件发生器

性能测试不要循环发送几千条单点 stdin 更新。使用 Python 的 `plan_events()`
先生成紧凑真值，再用 `generate_events()` 发出同一批：

```python
truth = outstation.plan_events(
    "analog_input",
    4096,
    scenario_id="burst-4096",
    seed=20260831,
    start_sequence=0,
    timestamp_base_ms=1700001000000,
    point_span=1024,
    interval_us=0,
)
truth.write("evidence/local/burst-4096-truth.json")
emitted = outstation.generate_events(
    "analog_input",
    4096,
    scenario_id="burst-4096",
    seed=20260831,
    start_sequence=0,
    timestamp_base_ms=1700001000000,
    point_span=1024,
    interval_us=0,
)
assert emitted == truth
```

point type 只接受 BI/AI。底层 `plan_events()`/`generate_events()` 的结构上限是
每请求 1～65,535 条，`point_span` 不超过 `point_count`，DNP3 时间不得溢出
48 bit。这个上限不等于一次完整性 benchmark 的安全块大小：
`LocalEventLoad`/`run_local_event_benchmark()` 强制单块最多 4,096 条，并要求
`event_count <= event_buffer_capacity`，因为 master unsolicited 队列固定保留
4,096 条。更长事件流必须拆成多个逐块对账的请求。

`interval_us=0` 是 burst；正数是有节奏事件，单个请求总节奏时长最多 55 秒。
定速模式以单调时钟的绝对 deadline 调度，并逐事件 Apply，避免相对 sleep 的
累计漂移或把整批事件瞬间提交。索引、布尔/float32 值和时间都由 seed/sequence
复现。`records_sha256` 对 master 实际接收语义使用固定的
`[kind,index,value]\n` canonical form，可直接转为 `CaptureEventManifest`。

更高层的突发/持续块 benchmark 使用
`config/local_event_profile.example.json` 与 `run_local_event_benchmark()`，详见
`docs/PERFORMANCE_AND_SOAK_GUIDE.md`。runner 每块必须同时满足 native capture
摘要匹配、master unsolicited 队列排空数量匹配，且两层 overflow/drop 均为 0；
只看其中一层不能证明无丢失。

严格格式见 `schemas/local-outstation-request.schema.json`。此 stdin 协议仅用于本机测试工具，与 `dnp3-master-host.exe` 的公共 NDJSON 协议不是同一个兼容性边界。

## 控制计数快照

`snapshot()` 返回 BOS/AOS 当前值及以下计数：

- `operation_count`
- `crob_operation_count`
- `analog_operation_count`
- `select_before_operate_count`
- `direct_operate_count`
- `direct_operate_no_ack_count`

这些计数可证明测试没有悄悄重发控制。例如，一次 CROB 操作加恢复、一次 AO 操作加恢复应得到 `operation_count == 4`。

## 边界与安全

- EXE 强制绑定 `127.0.0.1`，不能把它部署成网络可访问的生产从站。
- `--point-count` 和 `--event-buffer-capacity` 只改变本机启动规模，不代表真实 EMS 容量。
- 它不会模拟真实 EMS 的联锁、权限、时序、事件死区、队列容量、故障码和厂商差异。
- 不要把本机测试值、端口或成功结果复制为真实 EMS 的 PICS/点表结论。
- 真实控制仍必须经过私有 PICS、场景计划、逐次控制选择、安全授权、反馈读回和事故锁流程。
