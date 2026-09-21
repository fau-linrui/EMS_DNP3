# 工程架构基线（0.6.1）

## 可移植边界

自动化框架只依赖 `dnp3_master` Python 公开 API，不依赖 C++ 或 OpenDNP3 类型。Python 通过严格、单会话、单在途请求的 NDJSON 协议管理 `dnp3-master-host.exe`；原生 host 通过 `IMasterBackend` 隔离固定 OpenDNP3 3.1.2 与未来扩展后端。

```text
pytest/业务断言
  -> dnp3_master client + pytest plugin
  -> stdin/stdout NDJSON v1
  -> HostController
  -> IMasterBackend
  -> OpenDnp3Backend
       -> TCP Channel + Master
       -> OpenDnp3ReadSupport
       -> OpenDnp3UnsolicitedSupport
       -> OpenDnp3CommandSupport
       -> ProtocolTrace (explicit opt-in stack logs)
  -> EMS Outstation
```

## 目录职责

| 目录 | 职责 | 0.6.1 状态 |
|---|---|---|
| `native/` | C++17 host、后端和原生测试 | TCP、Read/IIN、unsolicited、持续 capture、控制、安全联锁、stats 已接入 |
| `python/` | 可嵌入 pytest 的包与测试 | 类型模型、进程所有权、PICS/点表/场景计划/离线预检/证据/性能/soak/风险门禁和 fixtures |
| `config/` | 框架能力台账和 EMS 配置示例 | 426 条标准/框架能力及独立 PICS、点表、场景计划模板；真实配置不提交 |
| `schemas/` | NDJSON、EMS 配置、本机测试控制和预检报告格式 | v1 严格 Schema |
| `scripts/` | 工具链发现、构建、测试、自检、打包 | Debug/Release/ASan、离线校验和解包回环验收 |
| `third_party/` | 固定源码、归档和锁 | OpenDNP3 3.1.2 及依赖已固定 |
| `docs/standards/` | 标准、PICS、依赖与证据状态 | 缺失项显式保留 |

## 生命周期与并发模型

两种显式控制策略：LAB 使用操作人/DUT 授权和持久事故锁；SIMULATOR 由
`TcpConnectionConfig.simulator=True` 声明，不需要身份或事故存储。
两者均校验协议、保留有界等待并关闭不确定会话。pytest 模拟器 `connected_master`
按用例创建独立 host，避免一次失败污染整个 session；LAB 保持原 session host。
模拟器可批量/重复控制，计划中的审批、前置检查和恢复可选，反馈断言保留。
详见 [模拟器模式](SIMULATOR_MODE.md)。

- 一个 Python client 拥有一个 host 子进程；Windows 下 Job Object 保证父进程消失时回收整个子进程树。
- 一个 host 最多创建一个 Manager、一个 TCP Client Channel 和一个 Master。
- Python 用可重入锁保证同一时刻只有一个 NDJSON 请求在途；Read/命令支持层也拒绝重叠协议任务。
- Python 每个 client 拥有一个容量为 1 的 stdin 写入队列及一个 writer 线程，写入与
  响应等待使用同一截止时间；关闭时回收该线程及待写载荷。控制锁还覆盖结果校验和
  事故处理，防止在不确定结果尚未处置时开始下一条操作。
- 断开顺序为：停止/取消命令和 Read -> 取走共享资源 -> Master Disable/Shutdown -> Channel Shutdown -> Manager Shutdown -> 清除安全令牌。
- OpenDNP3 回调只写入有界的任务状态/测量结构、capture 或 trace 队列；不会等待 Python 或 stdout。capture worker 在 native 层聚合，不构造跨任务逐点 JSON 数组。
- stdout 由主协议线程独占，只输出 JSON；诊断写 stderr。

capture worker 在全部成员初始化完成后才启动。Python 启动阶段遇到
`KeyboardInterrupt` / `SystemExit` 会先回收已创建的进程、Job 和已启动的 IO 线程，
再传播原中断；不能依赖尚未成功进入的上下文管理器来执行清理。

## Read 数据路径

`integrity_poll`、`class_poll` 和 `read` 为一次性同步任务。每个任务拥有独立 task ID、deadline、IIN 起始序号、接收序号、最多 4096 条分片记录和有界 MeasurementStore；共享 IIN 观测存储最多 1024 条。

detail 模式保留并返回逐点结果；summary 模式只累计计数，不保留或返回逐点记录。超过 `max_measurements`、分片上限或当前任务 IIN 窗口发生丢失时返回 `QUEUE_OVERFLOW`，不会静默丢数据。任务结果同时包含原始/解析 IIN、OpenDNP3 task completion、开始/完成时间和分片摘要；EMS 场景还会拒绝 IIN2.0/2.1/2.2，避免 task success 掩盖请求错误。

读任务等待超时的判定在任务锁内固定；迟到的完成回调不能再把本次调用升级为成功，
避免成功状态与较早的 IIN 快照拼接。该行为不发送重试，也不改变单在途请求边界。

当前 master 的自动启动完整性、event scan 和 unsolicited class mask 均关闭，避免建立连接时产生不可控后台任务；调用方必须显式发起 Read 或 `enable_unsolicited`。`OpenDnp3UnsolicitedSupport` 是跨请求存活的 ISOE handler，只收集 unsolicited 回调，使用默认 4096 条的 drop-oldest 队列，并记录会话、分片和接收顺序；disable/断开后的事件不得继续进入队列。原始 Confirm 丢失、序号回绕和重发故障注入仍属于后续互操作任务。

## 原始报文观测路径

`ProtocolTrace` 经 OpenDNP3 公共日志回调接收带 session/sequence/monotonic
time 的有界文本/HEX 记录。默认关闭，connect 前显式开启，不改 TCP 路径或 vendored
源码。回调只向有界队列复制数据，NDJSON 主线程通过 `trace.read` 批量消费；丢弃、
截断和 UTF-8 替换均可见。disconnect 后停止并排空，队列不在正常 disconnect 时清除。

Python `trace.py` 有界跨批重组 LPDU/传输层/APDU，保留原始字节并提供解析结果；
它与 SOE 测量、Read 结果和 measurement capture 相互独立，不改变控制判定。
观测边界是已编码 TX / 链路校验通过 RX，不是网卡抓包；缺失损坏输入原始字节、
真实发送完成和 wire 时间证据，不把可读栈日志视作规范权威。详见
[报文 trace 指南](PROTOCOL_TRACE.md)。

## Capture、性能与 soak 路径

`MeasurementCapture` 与单次 Read store 正交，一个会话只允许一个 ACTIVE
capture。回调按 source filter 把紧凑对象放入 1～65,536 的有界队列，native
worker 聚合静态点 bitset、计数、队列水位和事件 SHA-256。静态完整性以
`kind+index` 范围为真值；事件完整性以外部 manifest 的总数与有序
`[kind,index,value]\n` 摘要为真值；observation 不虚构 missing。disconnect、
shutdown、deadline、drain timeout 和 overflow 都有明确无效终态。

`performance.py` 加载带源文件 SHA-256 的严格 Profile，逐轮验证总点数、kind
分布和 Group:Variation 分布，先跑无 capture/有 capture A/B，再计算最近秩
p50/p95/p99/max 和阈值。`process_metrics.py` 只采集 host 进程的 Windows API
CPU、working set、private bytes、句柄和线程；网络字节和 DUT 资源没有获批
来源时必须为 null。本机边界回归使用 BI/AI/BOS/AOS 各 4,096 点，共 16,384
点；公共示例 Profile 的较小默认点数不是已验证上限。

本机事件 benchmark 把同一批确定性事件送入两条独立证据路径：
`MeasurementCapture` 的有序摘要，以及 `OpenDnp3UnsolicitedSupport` 的 4,096 条
持久队列。runner 每块排空主动上送队列，并同时要求 capture overflow 和
unsolicited `dropped_total` 均为 0；单块因此被限制为最多 4,096 条。底层测试
从站允许更大的发生请求，只用于明确评审的分块/溢出场景，不能绕过该完整性边界。

`soak.py` 只编排 Read/Integrity/Class Poll，不发送或重试状态改变操作。它使用
watchdog、资源/延迟/失败固定容量、磁盘余量和证据字节上限；preflight、周期
checkpoint、轮转 anchor 和 final report 原子写入并形成 SHA-256 链。runner
还会排空 1,024 条有界 channel-event 队列，在状态快照之间审计短暂断线/重连；
事件 drop 会使连接历史不可证明并 fail closed。Profile 的 watchdog 必须严格
覆盖该场景 begin、Read、end/drain 的全部有界 RPC 预算。中断的 run 独立标为
`INCOMPLETE_INTERRUPTED`，不会跨进程续算时长。

## EMS pytest 场景编排

模拟 EMS 的便捷入口为 `simulator_suite.py` 和 `examples/pytest_simulator`。
严格私有 JSON 一次解析连接/点/控制/事件配置，pytest 仍保留 SIMULATOR/PICS/能力门禁；
配套 runtime 按有限祖先路径发现，校验版本及矩阵 SHA-256，再经真实 hello 验证。
辅助层只组合公开 API，按用例拥有会话并先执行只读探针，不改变 native/NDJSON；
不生成外部信号、不发送时间同步/Restart。详见 [入门指南](SIMULATOR_QUICKSTART.md)。

`ems_test_plan.py` 是 Python/pytest 层的业务编排边界，不修改 NDJSON 或 OpenDNP3 协议层。它在 DUT 连接前加载最多 1 MiB 的严格 JSON，拒绝重复键、未知字段、非有限/越界值、重复场景、无效点引用和不完整恢复闭环，并把每个场景映射为能力矩阵 marker。

公开点表只描述可读对象；场景计划单独描述完整性/Class 读取、外部触发的主动上报期望以及控制闭环，二者在收集阶段交叉校验。LAB 下 `examples/pytest_ems` 每次只接受一个精确选择的控制场景，按基线读回、操作、确认后读回、恢复和恢复读回执行；操作后状态未确认则停止且不盲目恢复。SIMULATOR 可批量执行已启用场景，审批、前置和恢复可选；新入门套件不自动恢复。两种模式均不重试控制。

`ems_profile.py` 是 PICS 的唯一严格解析入口，pytest 插件和 `preflight.py` 共用它。离线预检进一步把 PICS、点表、场景计划与能力矩阵交叉计算，在没有 IP/端口且不启动 host 的情况下列出所需能力、blocker、warning 和输入哈希。预检 `READY` 只表示配置门通过，不提升互操作证据等级。

## 本机有状态端到端回归

`dnp3-local-test-outstation.exe` 强制绑定 `127.0.0.1`。默认提供两个 BI/AI/BOS/AOS 索引；性能启动参数可把这四类主点扩展到 1～65,535，并配置事件缓冲。测试控制协议可以显式制造单点 G2V2/G32V7 事件，也可按 seed/sequence/时间和节奏产生最多 65,535 条的底层确定性事件请求；高层无丢失 benchmark 单块最多 4,096 条。定速请求按单调时钟绝对 deadline 逐事件 Apply。G12V1 与 G41V1～V4 命令会改变对应 BOS/AOS 静态反馈，并记录 SBO/Direct/No-Ack 操作计数。

`test_ems_native_scenarios.py` 把公开 EMS pytest 场景模板接到真实 Python client、native host、OpenDNP3 master 和该测试从站，覆盖逐点 Static/Integrity/Class、主动上报以及控制/读回/恢复。该从站与主站仍是同栈，因此只属于本机工程证据。

## 控制数据路径与安全门

Python 仅公开 `CrobCommand` 和四种严格类型的 `AnalogOutputCommand`。命令数组最多 256 点，同类型/同索引重复在进入后端前被拒绝；已实现命令不能通过公共原始 `request()` 绕过类型 API。结果按原请求 ordinal/type/index 关联每个点，summary 和唯一性也做交叉校验。Command Status 经过显式 IEEE 1815-2012 适配，OpenDNP3 的后续版本别名仅作为后端诊断字段保留。固定栈对已识别值保留数值，但会把未知线上值 19～125 折叠为 127；`TIMEOUT`、2012 保留区和 raw 127 歧义均触发事故锁、令牌清除和 host 销毁。

以下四道门针对 LAB 模式；SIMULATOR 的会话令牌由 host 自动授权，不需要身份、审批或事故存储：

1. pytest 收集阶段要求用例同时带 `dnp3_dut`、能力 ID 和 `dnp3_state_changing`，并得到显式命令行/环境授权及 operator/DUT ID；未标记用例即使整次运行带了解锁参数也只能得到只读连接。
2. `connect` 只有在 `environment=LAB`、`allow_state_change=true` 和两个 ID 均有效时才生成 128-bit 会话令牌；后续每条命令必须携带正确令牌。
3. 私有 `ems_test_plan.local.json` 中的场景必须 `enabled=true`、具有非占位授权引用，并定义同类型/同索引的恢复命令和等于初始基线的恢复期望。
4. 每次 pytest 调用必须显式传 `--dnp3-control-scenario <精确ID>`；该选择故意没有环境变量回退，且一次只能选择一个。已授权状态改变测试会拒绝 pytest-xdist；控制模板还记录本进程已尝试的场景并拒绝 rerun/repeat。

Python 客户端不公开令牌属性，只在内存中自动附加；高层连接结果和诊断会移除/过滤令牌，断开或进程退出后销毁。该机制只防误操作，不是认证/授权/SAv5。

LAB 状态改变还要求持久 `SafetyIncidentStore`。控制响应超时、host 交换失败、提交异常、结果错配/损坏、结果自报不确定，或点状态无法证明确定拒绝时，客户端写入不含控制值/DUT 明文的事故锁，清除令牌并销毁 host；后续进程查锁，独立读回和明确确认后归档。该层不提供多机分布式锁或身份认证。SIMULATOR 对不确定结果同样清令牌并销毁 host，但不访问持久事故锁。

## 能力与证据模型

`config/capability_matrix.csv` 是“标准要求/框架实现状态”的唯一台账，其中 `dut_pics_status` 必须保持 `UNKNOWN`；它不能冒充某一台 EMS 的 PICS。每台 DUT 的 `SUPPORTED/NOT_SUPPORTED/UNKNOWN` 单独保存在未提交的 `ems.local.json`，运行时再叠加到框架台账。对象目录只能证明目录覆盖，完整标准声明还需要逐条 requirement catalog（shall/shall not/conditional）及可追踪测试。`hello.capabilities` 只暴露当前实现的子集，并带实现 revision 和本机验证范围。当前本机端到端从站也使用 OpenDNP3 3.1.2，因此相关能力保持 `IMPLEMENTED_UNVERIFIED`；独立端/真实 EMS 证据齐全后才能升级。

构建时 `build-info.json` 固定 host 版本、Git commit、工作区 clean/dirty/unavailable 状态、OpenDNP3 commit、构建配置、目标架构、依赖锁哈希和能力矩阵哈希。pytest `EvidenceRecorder` 为每次运行原子生成脱敏 manifest/结果，私有 PICS、点表和场景计划只记文件名、大小和 SHA-256，已知项目/测试/host/输入/证据路径会替换为占位符。能力行升级到任一 `VERIFIED_*` 时，`evidence` 引用必须追加实际文件的 `#sha256=<64 hex>`，验证器会读取文件复算；仅有可变路径不能作为可审计证据。正式证据应使用 `git_worktree_state=clean` 的构建并保存这些文件，不应只记录 EXE 文件名；任意测试输出仍需人工审查后才能外发。

`pytest-results.json` 的每条测试具有运行内唯一的 `case_id`（如 `case-000001`）。
`nodeid` 只是脱敏/截短后的显示名称，可能重复，不得用作唯一键。内部按原始 nodeid
摘要关联 setup/call/teardown；原始 nodeid 及该摘要均不输出。跨运行关联不能使用
这个运行内编号，应结合测试定义及受控配置另行处理。

发布脚本把安装树写入 `package-manifest.json`，再用固定时间戳、排序条目和固定压缩参数生成 ZIP/SHA-256；验证阶段在新目录解包、逐文件校验并执行真实本机回环，其中还包含 BI/AI/BOS/AOS 共 8 点的精确静态 capture。它保证相同安装树的 ZIP 字节稳定，但不声称 MSVC 输出本身已达到跨机器可复现。

安装树的源码输入由 `scripts/package-source-files.json` 明确逐文件映射，原生制品仅
复制两项已知 EXE 和 build-info。不会递归复制仓库目录；未列入允许清单的本地文件
不读取、不复制。归档前要求文件集合恰好等于允许清单、原生制品及单个 wheel，
拒绝额外文件、符号链接和 reparse point。

## 后续扩展顺序

T15a～T15d 的 capture 与 T16a～T16d 的本机性能/soak 工具链已完成；后续优先在内网完成 H01/H02、H08b/H09b 的真实 EMS Profile、独立参考端和正式环境证据，再按 PICS 单项增加原始故障时序、时间同步、Restart/Freeze/Assign Class、其他承载、经典对象缺口、高级事务和安全功能。不得扩大单会话/单在途 RPC 边界，除非有独立设计与迁移任务。

上述扩展是通用候选，不是当前模拟 EMS 基本验证的前置。当前需求已明确排除时间同步、
Restart 和外部模拟器触发接口开发；TCP 角色已固定为 pytest 主动连接 EMS 监听端。

具体阻塞、输入和验收见 `docs/INTRANET_HANDOFF_REMAINING_TASKS.md`。
