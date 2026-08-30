# OpenDNP3 3.1.2 缺口分析

> 状态：`IN_PROGRESS`。TCP Client、核心 solicited Read/IIN 和有响应控制的公共 API 已完成定向复核与本机同栈测试；其余能力仍需按真实 EMS PICS 逐项分析。任何本机结论均不等同于独立互操作。

## 固定依赖记录

| 字段 | 当前值 |
|---|---|
| 目标版本 | 3.1.2 |
| 源码位置 | `third_party/opendnp3/` |
| 完整 commit | `26b4c01e4839bbbda8866655e086471c4917ee53` |
| tag / tag object | `3.1.2` / `523f7d2d6c65aa671f594fc63c3adcd74e81407d` |
| Git tree | `18fedf6a7cf5aa98f7556afafec848d7e61f8f24` |
| 源码 ZIP SHA-256 | `7cb1a8a84f95c05b579a48543687a78c7dc9e3c394883419a92355a4aa6c1d5f` |
| 构建依赖锁 | `third_party/opendnp3-dependencies.lock.json` |
| 离线依赖 | Asio `asio-1-16-0`；exe4cpp `fb878a4...`；ser4cpp `3c449734...` |
| 许可证 | `LICENSES/opendnp3/LICENSE`、`LICENSES/opendnp3/NOTICE` |
| 自动校验日期 | 2026-08-29 |
| 人工源码/API 最终复核人 | MISSING |

## 复核判定方法

每个能力分别判断：

1. codec 能否表达对象、功能码和限定词；
2. Master 公共 API 能否发起事务；
3. 回调能否无损交付值、flags、时间、IIN 和状态；
4. 框架是否有完整事务、超时、取消和清理；
5. 是否有非同一实现的互操作/一致性证据。

只存在枚举、生成对象或 decoder 不能判定端到端支持。状态含义：

- `IMPLEMENTED_UNVERIFIED`：框架已接入并有本机证据，缺独立/DUT 证据。
- `UNSUPPORTED_BY_BACKEND`：固定公共 API 无法安全表达，运行时明确失败。
- `BLOCKED`：尚未完成源码/标准/PICS/安全前置分析。

## 已完成定向复核

| 能力 | 固定源码/API 与本项目证据 | 当前结论 |
|---|---|---|
| TCP Client | `DNP3Manager::AddTCPClient`、`IChannel::AddMaster`、`IMaster::Enable/Disable/Shutdown`、`IChannelListener::OnStateChange`；`OpenDnp3Backend.cpp`；`test_host_tcp.py` | `IMPLEMENTED_UNVERIFIED`：connect/timeout/reconnect/disconnect 本机已测 |
| 一次性 Integrity/Class/自定义 Read | `IMasterOperations::Scan/ScanClasses`、`Header` 工厂、`IMasterTaskCallback`；`OpenDnp3ReadSupport.cpp` | `IMPLEMENTED_UNVERIFIED`：范围、multi-header、summary、deadline 和 cancellation 本机已测 |
| BI/DBBI/BOS/Counter/Frozen Counter/Analog/AOS | `ISOEHandler` 公共 overload；类型化 visitors | `IMPLEMENTED_UNVERIFIED`：值/index/raw flags/顺序交付本机已测 |
| Octet String、TimeAndInterval 及其余公开 SOE 类型 | `ISOEHandler` overload；`OpenDnp3ReadSupport.cpp` | `IMPLEMENTED_UNVERIFIED`：公共回调均有归一化，完整长度/变体组合待独立测试 |
| IIN | `IMasterApplication::OnReceiveIIN`；有界 IIN store/completion gate | `IMPLEMENTED_UNVERIFIED`：raw/parsed 和 OBJECT_UNKNOWN 本机已测 |
| CROB SBO | `ICommandProcessor::SelectAndOperate(CommandSet, ...)` | `IMPLEMENTED_UNVERIFIED`：逐点状态、安全门和批次本机已测 |
| 有响应 Direct Operate | `ICommandProcessor::DirectOperate(CommandSet, ...)` | `IMPLEMENTED_UNVERIFIED`：CROB 路径本机已测，禁止自动重试 |
| G41 V1～V4 | `AnalogOutputInt32/Int16/Float32/Double64` + `CommandSet` | `IMPLEMENTED_UNVERIFIED`：混合批次和逐点关联本机已测 |
| Command Status | `CommandPointResult`、`CommandStatusSpec` | `IMPLEMENTED_UNVERIFIED`：公共枚举无损映射；全状态故障注入待独立端 |
| Unsolicited | `EnableUnsolicited/DisableUnsolicited`、持久 `ISOEHandler`、有界 SOE 队列 | `IMPLEMENTED_UNVERIFIED`：FC20/FC21、G2V2/G32V7、FC130 接收、禁用和溢出已做同栈本机回归；Confirm 丢失、重发、重复及序号回绕仍待独立故障注入 |
| Direct Operate No Response | 3.1.2 `ICommandProcessor` 公共接口仅提供结果回调型 Direct Operate | `UNSUPPORTED_BY_BACKEND`：明确失败，不用有响应命令模拟 |

## 尚未完成/需按 PICS 决策

| 能力族 | 主要缺口 | 当前状态 |
|---|---|---|
| Unsolicited 原始故障时序 | 基线路径已实现；缺 Confirm 丢失、重发/重复、序号回绕、启动空响应和独立端抓包 | 基线 `IMPLEMENTED_UNVERIFIED`；故障/互操作证据 `BLOCKED` |
| 时间同步 | Delay Measure/Write Time、Record Current Time 流程和 DUT 延迟预算未接入 | `BLOCKED`（T13） |
| Restart/Freeze/Assign Class/周期扫描 | 事务 API、风险门和真实 DUT 副作用未实现 | `BLOCKED`（T14，必须拆卡） |
| 持续 capture/性能 | 只有单次 detail/summary 和有限 stats；无 capture/network bytes/resource timeline | `BLOCKED`（T15/T16） |
| TCP Server/TLS/UDP/Serial | 后端/构建或 API 可用性未按目标拓扑复核 | `BLOCKED` |
| Group 0 Device Attributes | 对象支持、读取语义和 Profile 断言未闭环 | `BLOCKED` |
| Group 31/33 Frozen Analog、G34 Deadband | codec/回调/公共 Header/事务需逐项确认 | `BLOCKED` |
| Group 13/43 Command Event | 公开回调存在性与端到端交付未闭环 | `BLOCKED` |
| G50V1/V2、Group 80 主站读路径 | 特定读取/写入流程和公开 Header 能力未闭环 | `BLOCKED` |
| Group 110 全长度组合 | 公共 OctetString 回调已接入，只测一个本机长度 | `IMPLEMENTED_UNVERIFIED`，全组合待测 |
| 广播/self-address/特殊限定词 | 公共 API 表达能力和链路行为未闭环 | `BLOCKED` |
| File/Group 70 | 无完整文件事务状态机 | `BLOCKED` |
| Data Set/83/85–88 | 无完整业务事务 | `BLOCKED` |
| Virtual Terminal/112–113 | 无完整业务事务 | `BLOCKED` |
| SAv5/120–122 | 3.1.2 无项目批准的完整实现；安全前置输入缺失 | `BLOCKED` |

## 不得越过的证据边界

- `native/tests/local_outstation_main.cpp` 和 `opendnp3_read_integration_tests.cpp` 使用相同固定栈，只能作为工程回归。
- 修改/扩展 OpenDNP3 fork 后用该 fork 两端互测，仍不能算独立互操作。
- `hello.capabilities` 只列已接入能力；能力矩阵可能包含尚未实现的完整 IEEE 目录。
- 无 PICS 时 `dut_pics_status` 必须保持 `UNKNOWN`。
- 无不可变证据时不能使用 `VERIFIED_INTEROP` 或 `VERIFIED_CONFORMANCE`。

## 下一步

优先取得真实 EMS PICS、点表、连接参数和独立端，并先关闭 `ems_device_profile.md` 的 D01～D09，再执行 `docs/INTRANET_HANDOFF_REMAINING_TASKS.md` 的 H01～H04。当前操作约定提到 FC6，但固定 OpenDNP3 3.1.2 公共 API 不能安全表达 no-response 控制；在厂商确认、低层实现评审和独立证据前继续明确失败。其余能力只在 PICS/项目目标需要时逐卡复核，避免为目标 EMS 不支持的能力建立高风险自定义协议栈。
