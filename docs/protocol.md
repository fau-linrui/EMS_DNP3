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
| `stats` | 返回 host、channel、local queue 统计及缺失网络字节/capture 的明确限制 |
| `connect` | 创建 Manager -> TCP Client Channel -> Master，等待通道 OPEN |
| `disconnect` | 取消任务并按顺序关闭 Master/Channel/Manager，令牌失效 |
| `wait_event` | 等待并消费有界通道状态事件 |
| `integrity_poll` | 一次读取 Class 0 和 Class 1/2/3 |
| `class_poll` | 一次读取选择的事件 Class 1/2/3 |
| `read` | 执行 1～64 个严格 Header 的一次性 Read |
| `select_and_operate` | 有响应 CROB/Analog Output SBO 批次 |
| `direct_operate` | 有响应 CROB/Analog Output Direct Operate 批次 |
| `shutdown` | 清理后返回 SHUTTING_DOWN，刷新响应并退出 |

`capture.begin` 是保留的已知命令，但当前返回 `UNSUPPORTED_BY_BACKEND`。`hello` 不会把它列入 `supported_commands`。

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
"safety": {
  "environment": "LAB",
  "allow_state_change": true,
  "operator_id": "approved-operator-or-ticket",
  "dut_id": "lab-asset-id"
}
```

四项条件满足时，connect result 中一次性返回 32 个十六进制字符的 `safety_token`，有效期只到 disconnect 或进程退出。`get_status` 只返回锁状态，不回显令牌。令牌是防误操作联锁，不是安全认证。

## wait_event

参数 `timeout_ms` 为 0～60000，`max_events` 为 1～256。结果包含 `events`、`timed_out`、`remaining`、`dropped_total`。事件包含单调 `sequence`、`session_id`、`type=channel_state`、`state`（CLOSED/OPENING/OPEN/SHUTDOWN）和 `monotonic_ns`。队列容量 1,024，溢出时丢最旧并累计 `dropped_total`。

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

支持 `all_objects`、`range8`、`range16`、`count8`、`count16`。group/variation 为 0～255；range 起止 0～255/65535 且 start <= stop；count 为 1～255/65535。Class Group 60 V1～V4 不允许 range qualifier。

成功结果包括：

```text
task_id, task_status, task_started, task_destroyed, return_mode,
measurements, summary, fragments, iin, timings
```

每条 detail measurement 包含接收序号/时间、kind、group/variation、qualifier 原始/解析值、index、value、flags、DNP3 时间和质量、是否事件、header/fragment index 及 source。类型特有值放在额外字段中。`summary` 给出总数、详情数、溢出、序号范围、按 kind 和 group:variation 计数。

`iin` 同时保留 `raw_hex`、两个原始 octet、解析 bits 和有界观测信息。超过上限返回 `QUEUE_OVERFLOW`，错误 details 中保留 `operation_result`，不会静默成功。

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

成功结果包含 task/mode/status/timing、`all_success`、`execution_uncertain`、summary 和一一对应的 `point_results`。每点保留 header index、point index、CommandPointState 原始/解析值、CommandStatus 原始/解析值和请求副本。调用方必须检查每点状态。

命令超时返回 `RESPONSE_TIMEOUT`，details 明确 `execution_uncertain=true`、`may_still_execute=true`、`automatic_retry_safe=false`。不得自动重试。

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
