# DNP3 master host NDJSON protocol v1

`dnp3-master-host.exe` 只在 stdin/stdout 上使用 UTF-8 JSON Lines。普通 pytest 代码应调用 Python client，不建议手写本协议；本文供调试和后端扩展使用。

## 传输约定

- 一行一个请求；每个被处理请求恰好产生一个响应。
- 接受 LF/CRLF；拒绝 BOM、非法 UTF-8、空行、重复 JSON 键和非对象 `params`。
- stdout 只包含协议 JSON；日志和致命启动错误写 stderr。
- 默认单行上限 1 MiB；`--max-request-bytes` 可设 64 B～16 MiB。
- JSON 最大嵌套 64 层；ID 为 1～128 字节 ASCII token，命令为 1～64 字节 ASCII token。
- 最近 4,096 个请求 ID 不得复用。
- host v1 只有一个会话和一个在途请求。

请求：

```json
{"schema_version":1,"id":"req-1","cmd":"hello","params":{}}
```

成功：

```json
{"schema_version":1,"id":"req-1","ok":true,"result":{}}
```

失败：

```json
{"schema_version":1,"id":"req-1","ok":false,"error":{"code":"INVALID_REQUEST","message":"...","details":{}}}
```

客户端只能按 `error.code` 和结构化 `details` 分支，不能匹配可读 message。Schema 位于 `schemas/request.schema.json` 和 `schemas/response.schema.json`。

## 已实现命令

| 命令 | 行为 |
|---|---|
| `hello` | 返回 host/backend/build 身份、限制、能力和实现命令列表 |
| `get_status` | 返回 READY/CONNECTING/CONNECTED、通道、会话、安全锁和请求计数 |
| `stats` | 返回 host、channel、local queue、capture 快照及缺失网络字节的明确限制 |
| `connect` | 创建 Manager -> TCP Client Channel -> Master，等待通道 OPEN |
| `disconnect` | 取消任务并按顺序关闭 Master/Channel/Manager，令牌失效 |
| `wait_event` | 等待并消费有界的通道状态事件；不返回测点变化 |
| `enable_unsolicited` | 发送 Enable Unsolicited，显式启用选定的 Class 1/2/3 |
| `disable_unsolicited` | 发送 Disable Unsolicited，显式禁用选定的 Class 1/2/3 |
| `wait_unsolicited` | 等待并消费持久、有限容量的主动上送测量队列 |
| `capture.begin` | 启动本会话唯一的持续有界 measurement capture |
| `capture.progress` | 返回准确 capture ID 的非消费只读快照 |
| `capture.end` | 停止接收、在有界时间排空并幂等返回终态 |
| `integrity_poll` | 一次读取 Class 0 和 Class 1/2/3 |
| `class_poll` | 一次读取选择的事件 Class 1/2/3 |
| `read` | 执行 1～64 个严格 Header 的一次性 Read |
| `select_and_operate` | 有响应 CROB/Analog Output SBO 批次 |
| `direct_operate` | 有响应 CROB/Analog Output Direct Operate 批次 |
| `shutdown` | 清理后返回 SHUTTING_DOWN，刷新响应并退出 |

`hello`、`get_status`、`stats`、`disconnect` 和 `shutdown` 只接受空 `params`。

## connect

```json
{
  "host": "192.0.2.10",
  "port": 20000,
  "local_adapter": "0.0.0.0",
  "connect_timeout_ms": 5000,
  "retry": {"min_ms": 1000, "max_ms": 60000},
  "link": {
    "master_address": 1,
    "outstation_address": 1024,
    "keep_alive_timeout_ms": 60000
  }
}
```

只有 `host` 必填。端口 1～65535；连接超时 50～300000 ms；退避 10～300000 ms 且 min <= max；keep-alive 1000～86400000 ms。两个链路地址必须不同，范围 0～65519，不接受特殊/保留地址。

状态改变会话还需：

```json
{
  "safety": {
    "environment": "LAB",
    "allow_state_change": true,
    "operator_id": "approved-operator-or-ticket",
    "dut_id": "lab-asset-id"
  }
}
```

四项条件满足时，connect result 中一次性返回 32 个十六进制字符的 `safety_token`，有效期只到 disconnect 或进程退出。`get_status` 只返回锁状态，不回显令牌。令牌是防误操作联锁，不是安全认证。

## wait_event

参数 `timeout_ms` 为 0～60000，`max_events` 为 1～256。结果包含 `events`、`timed_out`、`remaining`、`dropped_total`。事件包含单调 `sequence`、`session_id`、`type=channel_state`、`state`（CLOSED/OPENING/OPEN/SHUTDOWN）和 `monotonic_ns`。队列容量 1,024，溢出时丢最旧并累计 `dropped_total`。

## 主动上送命令

`enable_unsolicited` 与 `disable_unsolicited` 的参数为：

```json
{"timeout_ms":5000,"classes":[1,2,3]}
```

`timeout_ms` 为 50～300000；`classes` 必须包含 1～3 个互不重复的 Class 1/2/3。结果包含 `task_id`、`task_status`、`task_started`、`task_destroyed`、`action`、`classes` 和 `timings`。调用方必须检查 `task_status`，不能仅凭收到响应判定启用或禁用成功。

`wait_unsolicited` 的参数为 `{"timeout_ms":10000,"max_events":256}`；等待时间为 0～60000 ms，单批数量为 1～256。结果包含 `session_id`、当前 `enabled/classes`、`measurements`、`timed_out` 和 `summary`。每条 measurement 的 `source` 为 `unsolicited`，并带当前 `session_id`。

主动上送队列默认容量 4,096，溢出采用 drop-oldest 并在 `summary.dropped_total` 中累计。任何非零丢弃数都表示事件流不完整，测试不得继续宣称 SOE 完整或顺序正确。断开会话会停止收集并清空该队列。Confirm 丢失、重发、重复检测及应用层序号回绕仍属于待独立验证项。

## Capture v1

`capture.begin` 的公共形态：

```json
{
  "mode":"static_set",
  "sources":["solicited"],
  "duration_limit_ms":15000,
  "mismatch_sample_limit":100,
  "queue_capacity":8192,
  "expected":{"point_ranges":[
    {"kind":"binary_input","start":0,"stop":1023},
    {"kind":"analog_input","start":0,"stop":1023}
  ]}
}
```

mode 为 `static_set`、`event_sequence` 或 `observation`；来源为 solicited、
unsolicited 或两者。点范围最多 256 段/1,000,000 点，队列 1～65,536，异常
样本最多 1,024，duration 为 100 ms～7 天。静态范围不允许同 kind 重叠。

`event_sequence` 不接收点范围，而接收严格 manifest：generator/version、
scenario ID、seed、连续 start/end sequence、event total、SHA-256 和固定
`match_rule=ordered_kind_index_value`。collector 对每条接收对象生成紧凑
`[kind,index,value]` JSON 加 LF 的有序 SHA-256；只有总数和摘要同时相等才把
`sequence_match` 置 true。`observation` 禁止 expected，完整性保持 unknown。

```json
{"capture_id":"cap-1-1"}
```

上式是 `capture.progress` 参数。`capture.end` 可再带
`drain_timeout_ms`（50～300000）。每个会话最多一个 ACTIVE capture；错误 ID
返回 `INVALID_STATE`。progress 不消费数据；同一 ID 的 end 幂等，直到下一次
成功 begin。断开和 shutdown 产生 `ABORTED`，deadline 产生 `TIMED_OUT`。

终态包含 offered/received/unique/duplicate/missing/unmatched、fragment、按 kind
和 GV 的计数、duration/throughput、当前/最大队列水位、overflow、有限 mismatch
样本、事件摘要和 source/scope。任何缺失、重复、意外点、事件摘要不一致、
counter/dimension/processing 错误或队列溢出都会 `valid=false`。队列溢出还返回
`QUEUE_OVERFLOW`，完整终态位于 `error.details.operation_result`，不得忽略。

严格请求/结果合同分别见 `schemas/request.schema.json` 和
`schemas/capture-result.schema.json`；Python 用例应调用类型化
`begin_capture/capture_progress/end_capture`。

## Read 命令

三个命令的公共参数：

```json
{"timeout_ms":5000,"max_measurements":10000,"return_mode":"detail"}
```

- `timeout_ms`：50～300000。
- `max_measurements`：1～1,000,000；即使 summary 不保存详情，也作为最大接收保护。
- `return_mode`：`detail` 或 `summary`。

`class_poll` 可加 `"classes":[1,2,3]`，1～3 项且不能重复。

`read` 必须加 1～64 个 Header：

```json
{
  "timeout_ms": 5000,
  "max_measurements": 10000,
  "return_mode": "detail",
  "headers": [
    {"group":30,"variation":0,"qualifier":"range16","start":0,"stop":9},
    {"group":1,"variation":0,"qualifier":"all_objects"},
    {"group":2,"variation":0,"qualifier":"count16","count":100}
  ]
}
```

支持 `all_objects`、`range8`、`range16`、`count8`、`count16`，分别对应 Q06/Q00/Q01/Q07/Q08。group/variation 为 0～255；range 起止 0～255/65535 且 start <= stop；count 为 1～255/65535。Class Group 60 V1～V4 不允许 range qualifier。OpenDNP3 3.1.2 公共 API 不能表达 Q02/Q09/Q39 的 32-bit range/count/index；需要这些限定符的场景必须在能力门禁中保持 `UNSUPPORTED_BY_BACKEND`，不能降级成 16-bit 后伪装执行。

成功结果包括：

```text
task_id, task_status, task_started, task_destroyed, return_mode,
measurements, summary, fragments, iin, timings
```

每条 detail measurement 包含接收序号/时间、kind、group/variation、qualifier 原始/解析值、index、value、flags、DNP3 时间和质量、是否事件、header/fragment index 及 source。类型特有值放在额外字段中。`summary` 给出总数、详情数、溢出、序号范围、按 kind 和 group:variation 计数；分片记录最多 4096 条，IIN 观测最多 1024 条。测量、分片或当前任务 IIN 窗口发生任何丢失都返回 `QUEUE_OVERFLOW`，不会以不完整数据成功。

`iin` 同时保留 `raw_hex`、两个原始 octet、解析 bits、有界观测信息、全局丢弃计数和当前任务窗口丢失计数。解析名称严格采用 IEEE 1815-2012 Table 4-3（例如 `NO_FUNC_CODE_SUPPORT`、`PARAMETER_ERROR`、`RESERVED_2`、`RESERVED_1`），原始值仍保留，避免名称转换丢失信息。OpenDNP3 task `SUCCESS` 只说明任务完成，不代表 DUT 接受了对象请求；EMS 场景模板会把 IIN2.0/2.1/2.2 视为请求失败，即使允许空响应也不会误通过。

## 控制命令

两种命令的参数：

```json
{
  "timeout_ms": 10000,
  "response_mode": "response",
  "safety_token": "0123456789abcdef0123456789abcdef",
  "commands": [
    {
      "type": "crob",
      "index": 0,
      "operation": "latch_on",
      "trip_close": "null",
      "clear": false,
      "count": 1,
      "on_time_ms": 100,
      "off_time_ms": 100
    },
    {"type":"analog_output_int16","index":1,"value":-123},
    {"type":"analog_output_int32","index":2,"value":123456},
    {"type":"analog_output_float32","index":3,"value":12.5},
    {"type":"analog_output_double64","index":4,"value":-9876.125}
  ]
}
```

命令数 1～256；index 0～65535；同 type/index 不能重复。CROB operation 为 `null/pulse_on/pulse_off/latch_on/latch_off`，trip_close 为 `null/close/trip`。整数和浮点值必须在对应类型范围内，拒绝 NaN/Infinity。

当前只支持 `response_mode=response`。`no_response` 明确返回 `UNSUPPORTED_BY_BACKEND`。

成功结果包含 task/mode/status/timing、`all_success`、`execution_uncertain`、summary 和一一对应的 `point_results`。每点保留 header index、point index、CommandPointState 原始/解析值、结构化 CommandStatus 和请求副本。`status` 是基于后端解码值生成的 IEEE 1815-2012 Table 11-7 规范视图，`status_raw` 是 OpenDNP3 解码枚举的 0～127 数值，`status_edition="IEEE1815-2012"`；后端后续版本名称仅放在 `status_backend`。13～18 会得到 `status="RESERVED"` 和 `status_reserved_2012=true`。固定 OpenDNP3 会把未知线上值 19～125 折叠为 127，因此此时 `status_wire_raw_unambiguous=false`，调用方不得断言线上原值一定是 127。必须检查每点状态和歧义标志，不能使用后端别名替代 2012 判定。

命令超时返回 `RESPONSE_TIMEOUT`，details 明确 `execution_uncertain=true`、`may_still_execute=true`、`automatic_retry_safe=false`。点级 `TIMEOUT`、2012 `RESERVED`（13～125）或解码值 127 的线上歧义也一律标记为需要人工读回；请求/结果关联缺失、重复或错配同样按协议损坏和不确定执行处理。不得自动重试。

## 错误码

| Code | 典型含义 |
|---|---|
| `INVALID_REQUEST` | 语法、字段、类型、范围、重复 ID/点或未知命令错误 |
| `REQUEST_TOO_LARGE` | 输入行超过限制，下一行仍可继续 |
| `SCHEMA_MISMATCH` | schema_version 不是 1 |
| `INVALID_STATE` | 当前 host/任务状态不允许操作 |
| `NOT_CONNECTED` / `ALREADY_CONNECTED` | 会话状态冲突 |
| `CONNECTION_TIMEOUT` | TCP 未在期限内 OPEN，资源已清理 |
| `SAFETY_INTERLOCK` | 控制会话未授权、令牌错误或已过期 |
| `ALREADY_EXECUTING` | 同类协议任务仍在执行 |
| `RESPONSE_TIMEOUT` | Read/控制未在期限内完成；控制执行可能不确定 |
| `TASK_FAILED` / `COMMAND_FAILED` | OpenDNP3 任务完成状态失败 |
| `UNSUPPORTED_BY_BACKEND` | 后端不能表达该已知能力，如 Direct Operate No Response |
| `UNSUPPORTED_BY_DEVICE_PROFILE` | DUT Profile 明确不支持（预留/适配层使用） |
| `PROTOCOL_ERROR` | 返回对象数或协议状态不满足框架不变量 |
| `QUEUE_OVERFLOW` | 有界结果容量不足，不能视为完整成功 |
| `PROCESS_SHUTTING_DOWN` | shutdown 后收到请求 |
| `INTERNAL_ERROR` | 未预期异常已被边界捕获 |

## pytest 场景计划不属于 NDJSON 协议

`config/ems_test_plan.example.json` 及 `ems_test_plan.py` 只在 Python 收集/业务编排层使用。它们把完整性/Class、主动上报和控制闭环转换成上述既有公开 API 调用，不会把计划、授权引用、反馈期望或恢复步骤发送给 host。host 仍只接收本文件定义的单条严格命令；控制计划也不能绕过会话令牌或持久事故锁。

同理，`dnp3-local-test-outstation.exe` 的 stdin 控制协议只用于回环测试工具，不属于 host 公共协议，也不会发往 EMS。其严格格式见 `schemas/local-outstation-request.schema.json`，使用方法与安全边界见 `docs/LOCAL_TEST_OUTSTATION.md`。
