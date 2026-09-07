# H08/H09 持续采集、性能、大点表与稳定性指南

本文对应 0.6.0 的 H08a/T15 与 H09a/T16a～T16d。本机工具代码已经完成；
H08b/H09b 仍需在内网填入真实 EMS 点表、批准阈值和环境证据并实际运行。

## 1. 先理解结论边界

框架现在可以自动完成以下工作：

- 跨多个 DNP3 回调持续采集 solicited/unsolicited 测量；
- 对静态点集合检查缺失、重复和意外点；
- 对确定性事件流按 `kind/index/value` 的有序 SHA-256 与外部真值比对；
- 在每类 1～65,535 个本机主点范围内产生突发或有节奏的 BI/AI 事件；
- 对大点表 Read 做无 capture/有 capture 的 A/B 测量；
- 记录 task 延迟 p50/p95/p99/max，以及 host 的 CPU、working set、private
  bytes、句柄和线程；
- 按 Profile 判阈值；
- 运行可中断的 read-only soak，使用 watchdog、磁盘余量、证据上限、原子
  检查点、SHA-256 链和轮转；
- 把中断、host 退出、连接次数超限、资源/证据上限、连续失败或阈值失败
  明确写成非通过状态。

本机主站和从站使用同一个 OpenDNP3 3.1.2，且运行在同一台机器。因此所有
内置报告都固定 `formal_dut_conclusion=false`。它们证明工具链工作，不证明真实
EMS 的性能、互操作性或 IEEE 一致性。

## 2. 代码和配置在哪里

| 内容 | 路径 |
|---|---|
| native capture collector | `native/src/MeasurementCapture.cpp` |
| capture 请求解析 | `native/src/CaptureConfig.cpp` |
| Python capture 模型/API | `python/src/dnp3_master/models.py`、`client.py` |
| 性能 Profile、A/B runner | `python/src/dnp3_master/performance.py` |
| Windows 进程资源采样 | `python/src/dnp3_master/process_metrics.py` |
| 24 小时 runner | `python/src/dnp3_master/soak.py` |
| 本机事件发生器/benchmark | `python/src/dnp3_master/local_outstation.py`、`local_benchmark.py` |
| 可复制 pytest 用例 | `examples/pytest_performance/` |
| 性能 Profile 示例 | `config/performance_profile.example.json` |
| 本机事件 Profile 示例 | `config/local_event_profile.example.json` |
| 严格合同 | `schemas/*capture*`、`schemas/*performance*`、`schemas/*soak*`、`schemas/local-event-*` |

Profile 加载器拒绝重复 JSON 键、未知字段、NaN/Infinity、越界数量和不一致
基线。报告可用 `write_json_report()` 原子保存；默认拒绝覆盖同名证据，并返回
文件 SHA-256。

## 3. 本机最短验证

先构建，再运行完整回归：

```powershell
.\scripts\build.ps1 -Preset windows-msvc-release
.\scripts\test.ps1 -Preset windows-msvc-release
```

只运行 H08/H09 回环用例时，在同一个 PowerShell 窗口设置 EXE：

```powershell
$env:DNP3_MASTER_HOST_EXE = (
  Resolve-Path '.\out\build\windows-msvc-release\bin\dnp3-master-host.exe'
).Path
$env:DNP3_TEST_OUTSTATION_EXE = (
  Resolve-Path '.\out\build\windows-msvc-release\bin\dnp3-local-test-outstation.exe'
).Path

.\.venv\Scripts\python.exe -m pytest -q `
  .\python\tests\test_performance.py
```

该测试会创建 4,096 个 BI、AI、BOS 和 AOS，共 16,384 点；验证 baseline/capture
A/B、对象总数和按 kind/GV 的精确分布、静态完整性、突发/有节奏事件摘要、
资源采样、短时 soak、检查点轮转和人工中断状态。它不会连接真实 DUT。

仓库中的 `performance_profile.example.json` 为了日常运行速度，默认使用每类
1,024 点，共 4,096 点；上面的 16,384 点是自动化边界回归实际覆盖值。两者都
不是对真实 EMS 容量或工具绝对上限的声明。

## 4. Capture v1 怎么用

一次会话最多有一个 ACTIVE capture。静态大点表示例：

```python
from dnp3_master import CaptureConfig, CapturePointRange, ReadHeader

started = master.begin_capture(
    CaptureConfig(
        mode="static_set",
        sources=("solicited",),
        duration_limit=15.0,
        point_ranges=(
            CapturePointRange("binary_input", 0, 1023),
            CapturePointRange("analog_input", 0, 1023),
        ),
        queue_capacity=4096,
    )
)
result = master.read(
    (
        ReadHeader.all_objects(1, 2),
        ReadHeader.all_objects(30, 5),
    ),
    return_mode="summary",
    max_measurements=2048,
)
terminal = master.end_capture(started.capture_id, drain_timeout=10.0)

assert result.summary["received_total"] == 2048
assert terminal.state == "FINALIZED"
assert terminal.valid is True
assert terminal.missing == 0
assert terminal.duplicates == 0
assert terminal.unmatched_total == 0
assert terminal.queue_overflow == 0
```

`capture.progress()` 是只读快照；`capture.end()` 对同一 ID 幂等，直到下一次
成功 begin。断开/退出会把活动采集改为 `ABORTED`；本地 deadline 到期为
`TIMED_OUT`。队列溢出返回稳定的 `QUEUE_OVERFLOW`，终态摘要位于
`HostCommandError.details["operation_result"]`。这些状态都不能当作完整数据。

三种 mode 的证据含义：

| mode | 真值 | 可以证明什么 |
|---|---|---|
| `static_set` | 有界 `kind + index` 范围 | unique/missing/duplicate/unmatched |
| `event_sequence` | 外部 manifest 的总数和有序 SHA-256 | 事件数量、顺序、kind/index/value 完全一致 |
| `observation` | 无 | 只统计观察值；完整性必须保持 unknown |

事件 canonical record 固定为紧凑 JSON 数组 `[kind,index,value]` 加 LF；模拟量
真值按线上 float32 归一化。不要自行改变格式或只比较事件总数。

## 5. 大点表性能 Profile

复制示例，不要直接修改公共模板：

```powershell
Copy-Item .\config\performance_profile.example.json `
  .\config\performance_profile.local.json
```

每个场景必须给出：

- operation、实际 Header/Class 和超时；
- 预热、正式测量、间隔和冷却；
- `max_measurements`；
- 每轮精确 `expected_objects_per_iteration`；
- 每轮精确 `expected_by_kind` 和 `expected_by_group_variation`；
- 可选 capture 配置；有 capture 的场景必须指向完全相同 population 的无
  capture baseline；
- 项目批准的延迟、CPU、内存、句柄、线程、增长趋势和 capture 开销阈值。

runner 同时校验总数、kind 分布和 GV 分布。即使总数相同，只要对象混淆也会
失败。Profile 原始字节的 SHA-256 会进入报告和 pytest 证据清单。

本机 Profile 使用 `LOCAL_LOOPBACK_ONLY`。真实 EMS 的私有副本只能使用
`TARGET_ENVIRONMENT_PENDING_REVIEW`；这个名称表示“待评审的目标环境证据”，
不是正式通过。

## 6. 本机突发与持续事件块

`config/local_event_profile.example.json` 包含 4,096-event burst 和 1,000 events/s
的 4,096-event paced chunk。定速模式按单调时钟绝对 deadline 逐事件 Apply，
不会用逐次相对 sleep 累积调度漂移或把 1024 条伪装成一个定速批次。发生器只绑定
`127.0.0.1`，每个请求最长 55 秒，提供 seed、sequence、时间戳和紧凑 SHA-256
真值。底层发生器合同允许单请求最多 65,535 条，但高层 Profile/Schema 和 runner
把单个 benchmark chunk 限制为最多 4,096 条，并额外要求
`event_count <= event_buffer_capacity`。这是因为 runner 会逐批清空并核验 master
的固定 4,096 条 unsolicited 队列；`dropped_total != 0` 或 drain 数量与 manifest
不同都会失败，不能只看 capture。
典型调用：

```python
from pathlib import Path
from dnp3_master import (
    Dnp3MasterClient,
    HostProcessConfig,
    TcpConnectionConfig,
    load_local_event_profile,
    run_local_event_benchmark,
    write_json_report,
)
from dnp3_master.local_outstation import LocalTestOutstation

profile = load_local_event_profile("config/local_event_profile.example.json")
with LocalTestOutstation(
    Path("out/build/windows-msvc-release/bin/dnp3-local-test-outstation.exe"),
    point_count=profile.outstation_point_count,
    event_buffer_capacity=profile.event_buffer_capacity,
) as outstation:
    with Dnp3MasterClient(
        HostProcessConfig(
            executable=Path(
                "out/build/windows-msvc-release/bin/dnp3-master-host.exe"
            )
        )
    ) as master:
        master.connect(
            TcpConnectionConfig(host="127.0.0.1", port=outstation.port)
        )
        enabled = master.enable_unsolicited((1, 2))
        assert enabled.task_status == "SUCCESS"
        try:
            for load in profile.loads:
                report = run_local_event_benchmark(master, outstation, load)
                write_json_report(
                    report,
                    Path("evidence/local") / f"{load.scenario_id}.json",
                )
                assert report["passed"], report
        finally:
            master.disable_unsolicited((1, 2))
            master.disconnect()
```

报告中的 `events_received`/`queue_overflow` 来自 native capture，
`unsolicited_queue.events_drained`/`dropped_total` 来自独立的 master 主动上送存储。
只有两条路径都与 manifest 一致且无丢失，`passed` 才可能为 true。

长事件流必须由调用方以多个不超过 4,096 event 的已评审有界 chunk 编排；当前 read-only soak
不会暗中生成事件，也不会替真实 EMS 开启/关闭 unsolicited。目标环境的事件
必须来自独立发生器或可审计的外部触发，不能使用本机从站真值冒充。

## 7. 在已有 pytest 框架中运行目标 benchmark

把 `examples/pytest_performance` 整个目录复制到已有框架。它会从 Profile 自动
为用例附加 FC1、对象、Qualifier、Class 和 observability capability marker，
并继续经过 PICS/框架双门禁。先只跑 benchmark：

```powershell
python -m pytest .\examples\pytest_performance\test_read_performance.py -q `
  --dnp3-host-exe .\bin\dnp3-master-host.exe `
  --dnp3-pics-file .\config\ems.local.json `
  --dnp3-capability-matrix .\config\capability_matrix.csv `
  --dnp3-performance-profile .\config\performance_profile.local.json `
  --dnp3-outstation-host '<EMS_IP>' `
  --dnp3-outstation-port <EMS_PORT> `
  --dnp3-unknown-policy error `
  --dnp3-evidence-dir .\evidence\local\pytest `
  --dnp3-performance-report-dir .\evidence\local\performance
```

这套示例只执行 Read/Integrity/Class Poll，不执行控制。若目标 EMS 规定事件只能
主动上送，性能 Profile 中不要编造 Class 事件 Read 负载；另建经过确认的
unsolicited 事件计划。

## 8. 24 小时 soak

示例 Profile 的 `target_duration_seconds=86400`。只有短时 benchmark 和配置评审
先通过后，才追加明确开关：

```powershell
python -m pytest .\examples\pytest_performance\test_read_performance.py -q `
  <与上一节相同参数> `
  --dnp3-run-soak
```

soak 不是 sleep：它循环执行经过真值校验的只读场景，定期采样 host 资源，
并消费 native 的有界 channel-event 队列来捕获采样间隔内的短暂断线/重连。
通道事件有任何 drop，连接历史即不可证明，运行会 fail closed。watchdog 必须
大于 capture begin、Read 和 end 三段 RPC 的已知总预算；单次执行返回后若仍超过
watchdog，同样失败。Profile 加载时会按每个场景计算该下界：无 capture 为
`task_timeout_seconds + 1`；有 capture 还要加 begin 的 2 秒，以及
`min(300, max(1, task_timeout_seconds)) + 1` 的 end/drain 请求预算。配置值必须
严格大于所有场景的最大下界。证据目录含：

```text
preflight.json
checkpoint-00000000.json ...
checkpoint-anchor.json          # 发生轮转后存在
final-report.json
```

检查点原子写入并形成 `previous_checkpoint_sha256` 链；达到数量上限时只保留
有界窗口和 anchor。磁盘余量或证据字节上限不足会 fail closed。Ctrl+C/停止
回调得到 `INCOMPLETE_INTERRUPTED`，不会与下次运行拼成 24 小时。

结束判定先检查停止请求和 host 存活，再判断目标时长；成功结束前还会读取通道
状态、排空连接事件、再次检查停止/存活，并保存最后一次资源样本。最后一次休眠
期间 host 退出或出现停止请求不能得到 `passed=true`；最终通道必须保持 OPEN，
连接事件不得丢失。

主要终态：

| status | 含义 |
|---|---|
| `COMPLETED` | 达到目标时长且所有阈值/完整性条件通过 |
| `COMPLETED_FAILED_THRESHOLDS` | 达到时长，但至少一个阈值失败 |
| `INCOMPLETE_INTERRUPTED` | 人工或外部停止 |
| `INCOMPLETE_HOST_EXIT` | native host 退出 |
| `INCOMPLETE_WATCHDOG` | 单场景超过 watchdog |
| `INCOMPLETE_CONNECTION_LIMIT` | 断开/重连 episode 超限 |
| `INCOMPLETE_RESOURCE_LIMIT` | 样本、磁盘或证据上限 |
| `INCOMPLETE_FAILURE_LIMIT` | 连续场景失败超限 |
| `INCOMPLETE_EVIDENCE_WRITE` | 检查点/最终报告无法可靠写入 |

只有 `status == "COMPLETED"`、`passed == true`、所有 overflow/drop 为 0、
失败数为 0 且全部阈值通过，才是一次完整 runner 结果。仍需 H09b 评审才能形成
真实 EMS 性能结论。

## 9. 内网仍必须完成的 H08b/H09b

内网 agent 不应重写 collector/runner，优先只填私有输入并执行：

1. 固定 EMS 厂商/型号/固件与正式 Device Profile/PICS。
2. 从批准点表生成 exact headers、kind/GV 分布、索引范围和事件 Class。
3. 取得业务批准的点数、事件率、延迟、CPU/内存/句柄/线程、增长趋势、
   重连次数和 24 小时有效时长阈值。
4. 准备与本项目不同实现、最好不同机器的独立参考端；确认其容量高于目标。
5. 记录测试机 CPU/内存/OS、电源计划、网卡、两端拓扑、参考端版本、DUT
   资源采集源和所有配置哈希。
6. 使用批准的 PCAP/链路计数取得网络字节和丢包证据；不可测字段保持 null，
   不允许估算。
7. 先执行短时目标 benchmark，再执行独占环境的连续 24 小时；保留原始
   Profile、报告、检查点、PCAP、构建信息和 SHA-256。
8. 由项目负责人评审后另行形成正式结论；不要修改报告内固定的
   `formal_dut_conclusion=false` 来制造认证结果。

没有批准阈值、独立真值、独占环境或 H08a/H09a 本机回归失败时，H09b 必须
停止。详细任务卡见 `docs/INTRANET_HANDOFF_REMAINING_TASKS.md`。
