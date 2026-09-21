# 在 pytest 中查看 DNP3 收发报文

`start_trace()`、`read_trace()`、`stop_trace()` 把 host 的双向 DNP3 协议栈日志
通过原有 NDJSON 通道交给 pytest。Python 同时保留日志原文和原始 HEX，并增量
重组链路帧、传输层分段和应用层报文。无需安装抓包驱动，也不插入 TCP 代理；
pytest 主站仍直接连接 EMS 的监听端。

默认关闭，原有 `read()`、控制、主动上报和 measurement capture 的接口不变。
这不是既有 `begin_capture()`：后者检查测量值集合/事件真值，trace 用于检查协议
交换。两者可以同时启用，但日志采集、传输和解析有额外开销，性能基线应保持相同设置。

## 1. 能看到什么，不能证明什么

| 数据 | 范围 |
|---|---|
| RX 原始 HEX | OpenDNP3 已通过链路校验的完整链路帧，包含链路头和 CRC |
| TX 原始 HEX | OpenDNP3 已编码、准备发送的链路帧；不是 socket 写成功或 EMS 已接收的证明 |
| 协议字段 | 从保留的字节解析链路/传输/应用层头及支持的对象；未识别载荷继续保留原始 HEX |
| 栈诊断 | 请求/响应头、对象和错误等日志，带会话、顺序、单调时间；自动产生的 Confirm 等也在观测范围内 |

`scope` 固定为 `opendnp3_stack`，不能写成网卡抓包：

- 不包含 TCP/IP 头、握手、TCP 重传、网卡级时间戳，也不是 PCAP。
- CRC 错误、非法链路控制、垃圾字节或截断输入可能只有栈错误日志，没有对应的完整原始字节。
- TX 可能编码后因断线未实际发出，时间/顺序是编码观测点，不是 socket 完成/网卡时间；
  不能按其数量证明线上发送次数。
- 栈可读日志本身可能已经截短，不能把文字描述当作逐字段无损解码或标准判定依据。
  本项目的 `truncated_records` 只统计本项目采集边界发生的截断/替换，不能检测栈内部先前的截短。
- `complete=true` 只表示当前 trace 采集/重组范围内未发现丢失或错误，不表示捕获了
  所有线上字节、解析了全部 DNP3 对象，也不构成 IEEE 一致性或真实 EMS 互操作结论。

业务通过与否仍按 `ReadTaskResult`、`CommandTaskResult`、主动上报测量和实际读回
结果判断。即使 trace 显示控制已编码，结果不确定时原有销毁会话规则仍生效；不能据此
自动重发控制，或使用 trace 绕过 SIMULATOR/LAB 的既有行为。

## 2. 开启和读取顺序

1. 启动 client，在 `connect()` **之前**调用 `start_trace(TraceConfig(...))`。
2. 正常连接 EMS 并执行原有 API；每次有界操作后调用 `read_trace()`，及时排空日志。
3. 操作结束后先 `disconnect()`，再 `stop_trace()`；这样关闭过程也在观测范围内。
4. 继续调用 `read_trace()`，直到 `batch.summary.queued_records == 0`，再关闭 client。

只能在没有活动 DNP3 会话时启停；已经 ACTIVE 时不能再次 start。STOPPED 中有未读
记录时不能用新 start 丢弃旧记录。停止只结束收集，不删除队列；同一 trace 的 stop
幂等。新 trace 使用新 ID，Python 自动管理该 ID，普通用例不用手写 NDJSON。

`disconnect()` 不会清除 trace；`close()`/host 退出会销毁内存中的剩余记录。
操作超时或不确定控制导致 host 被强制关闭时，尚未传回 Python 的日志可能无法恢复。
不要为了取日志重发原操作；需要可靠故障落盘或全网卡证据时，应另行设计独立采集。

## 3. 可复制到 pytest 的只读示例

先按 [Python/pytest 接入说明](python_client.md) 加载插件并配置配套 host、连接和
SIMULATOR/PICS。下例假定点号 0 是可读取的 G30V5；请改成实际点号。
`192.0.2.10` 等文档保留地址不是可直接运行的目标地址。

```python
import pytest

from dnp3_master import Dnp3MasterClient, ReadHeader, TraceConfig


def inspect_trace(batch):
    batch.assert_complete()
    # 只统计数量；不要默认把原始业务载荷打印进共享 CI 日志。
    return len(batch.records), len(batch.frames), len(batch.applications)


@pytest.mark.dnp3_dut
@pytest.mark.dnp3_capability("APP.FC.01.READ")
@pytest.mark.dnp3_capability("OBJ.G30.V5")
@pytest.mark.dnp3_capability("QUAL.Q01.REVIEW")
def test_read_with_protocol_trace(dnp3_host_config, dnp3_connection_config):
    # 使用未连接的新 client，而不是已经连接的 connected_master fixture。
    with Dnp3MasterClient(dnp3_host_config) as master:
        master.start_trace(TraceConfig(queue_capacity=16384))
        master.connect(dnp3_connection_config)
        result = master.read([ReadHeader.range16(30, 5, 0, 0)])
        inspect_trace(master.read_trace(max_records=1024))

        master.disconnect()
        master.stop_trace()
        # STOPPED 后不再产生记录；最大队列 65536，最多 64 批可排空。
        for _ in range(65):
            batch = master.read_trace(max_records=1024)
            inspect_trace(batch)
            if batch.summary.queued_records == 0:
                break
        else:
            pytest.fail("trace 队列未在上限内排空")

        assert result.task_status == "SUCCESS"
        assert not {
            "IIN2.0.NO_FUNC_CODE_SUPPORT",
            "IIN2.1.OBJECT_UNKNOWN",
            "IIN2.2.PARAMETER_ERROR",
        }.intersection(result.iin["bits"])
        assert len(result.measurements) == 1
```

这段代码演示成功路径的完整收尾；操作失败时 context manager 仍关闭 host，测试失败
不会因为有报文日志而被改成通过。若需要失败路径的诊断，应保留原异常，且只在 host
仍正常存活时额外读日志，不要让取日志失败覆盖原始错误。

## 4. 数据与容量

`TraceBatch` 包含 `summary`、`records`、`frames`、`applications`、`complete` 和
`issues`，可用 `to_dict()` 转为 JSON 兼容对象。`records` 是从 native 传回的原始日志；
`frames`/`applications` 是 Python 从原始 HEX 额外解析的结果，不是重新请求 EMS。
跨批次的不完整分段由解码器有界保留，在后续批次完成时才交付；因此一次 read 有日志
但没有完整 frame/application 并不一定是错误。必须完成 stop 后的最终排空才能检查尾部。

- `TraceConfig.queue_capacity` 默认 16,384 条，范围 1～65,536；这是**日志条数**，不是报文数。
  一帧通常包含多条 HEX/字段日志；容量不能直接换算为相同数量的 DNP3 帧。
- `read_trace(max_records=256, timeout=0.0)` 默认非阻塞；单批 1～1,024 条，等待 0～60 秒。
  它与其他 API 共享单在途 RPC，不在另一个线程里与 Read/控制并发调用。
  `timed_out` 只表示该批为空（包括 STOPPED 已排空），不是 EMS 响应超时。
- 每条日志包含 `sequence`、`session_id`、`monotonic_ns`、`logger`、`level`、`message`
  和 `message_truncated`。单调时间只用于该进程内排序/耗时，不是设备时间或 UTC 报文时间。
- native logger 最多 128 UTF-8 字节、message 最多 1,024 UTF-8 字节。超限截断或非法
  UTF-8 替换都会标记并计入 `truncated_records`。
- 队列满时丢最旧记录并累计 `dropped_records`；这些计数在同一 trace 内不会通过读取归零。
  回调不会等待 pytest/stdout，避免诊断消费者拖住协议线程。

### 程序化检查协议字段

`TraceFrame` 的 `raw_hex`/`raw_bytes` 包含链路 CRC，`payload_hex` 是去除 CRC 的
链路用户数据；`link` 提供链路头，`transport` 提供传输头或 `None`，`crc_valid`
是对保留字节的独立复算结果。`direction` 为 RX/TX（以主站为视角），
`first_sequence`/`last_sequence` 对应组成该帧的原始日志序号。

`TraceApplication` 是经过传输层重组的**一个应用层分片**，不自动把多个 application
分片合并成一次 Read/控制事务。`frame_sequences` 关联组成该 APDU 的链路帧；
`header` 包含 `function_code`、`function_name`、FIR/FIN/CON/UNS/sequence 及响应 IIN。
`objects` 按对象头保留 group/variation/qualifier、范围/数量及 `values`。示例：

```python
batch = master.read_trace(max_records=1024)
for frame in batch.frames:
    assert frame.crc_valid
    # 需要原始字节时读取 frame.raw_bytes，不要解析人类可读的 message 文本。
    assert frame.direction in {"RX", "TX"}
    # 仅在获准的本地调试中打印；原始 HEX 含业务载荷，不要放入共享 CI 输出。
    # print(frame.direction, frame.raw_hex, frame.link, frame.transport)

for app in batch.applications:
    if app.direction == "RX" and app.header["function_code"] == 130:
        for header in app.objects:
            if (header["group"], header["variation"]) in {(2, 2), (32, 7)}:
                received_values = header["values"]
                # 在本地执行点号/flags/value/time 等断言，不默认打印业务值。
```

当前对象值解码覆盖 G1V1/2、G2V1～3、G3V1/2、G4V1～3、G10V1/2、G12V1、
G20/G22 的 V1/2/5/6、G30V1～6、G32V1～8、G40V1～4、G41V1～4、G50V1/3、
G80V1；READ、Enable/Disable 等无值的对象请求头另行解析。支持“观察某对象字节”
不意味着主站公共 API 已实现该对象/功能的发送，也不会改变 FC6/32-bit qualifier 的
原有能力限制。

未知布局会保留整个 `app.raw_hex`，设置 `decode_error` 并把问题加入 `batch.issues`，
不会猜测剩余对象边界。NaN/Infinity 保留原始字节、置 `value=None` 并记录
`value_non_finite`；相对时间保留 `relative_time_ms`，不凭空猜测 CTO/绝对时刻。
`batch.summary.complete` 只反映 native 日志采集；`batch.complete` 还要求当前及此前
解析未发生缺失/不支持等问题，二者含义不同。`UNSUPPORTED_OBJECT`/
`UNSUPPORTED_QUALIFIER` 表示解码覆盖不足，原始字节仍然保留，不能将其误报为掉包。

默认 `read_trace()` 对丢失、采集截断、重组错误或未支持的语义解码抛出 `TraceIncompleteError`，异常的
`.batch` 保留该批原始数据与诊断。显式 `require_complete=False` 可以取不完整批次用于
排错，但不能把该 trace 当作完整解析证据；随后可用 `batch.assert_complete()` 强制检查。
仅此诊断异常不会重试业务命令，也不会自行关闭正常 DNP3 会话。

长时间主动上报应在每次有界等待后及时读取 trace；不要一次阻塞业务请求很久，再假定
有限缓存能保存全部历史。大型 Read 也可能在一次 RPC 内填满日志队列，须按观测目标
拆分读取/调整容量，或接受明确的不完整诊断。现有 24 小时 runner 不会自动保存全量
trace；需要长期落盘时由调用框架实现有限大小、轮转和磁盘上限，不能无限积累 list。

## 5. 移植、版本和敏感数据

同时更新 `dnp3_master` 包、配套 host、`build-info.json` 和能力矩阵；本次新增
`trace.py` 已进入可移植包的允许清单。只有新 Python 没有新 host 时，`start_trace()`
会明确拒绝缺少 trace 命令的旧 host，不会悄悄返回空数据。源码用户重新执行构建；
内网使用完整新可移植包后，不必编写 C++。

原始报文和日志可能含链路地址、点号、值、控制参数、时间和连接诊断。本功能默认不
写文件、不自动放进脱敏 evidence manifest，也不保证为日志内容脱敏。只有确需诊断
时显式启用；将 `to_dict()` 的结果写入受控本地目录并限制访问/大小/保留时间，外发前
人工检查。不要提交到 GitHub、公共 CI 日志或未获准的报告平台。

严格 NDJSON 字段和状态机见 [协议文档](protocol.md)，native 原始结果约束见
[`trace-result.schema.json`](../schemas/trace-result.schema.json)。
