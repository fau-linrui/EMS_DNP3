# 工程架构基线（0.5.1）

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
  -> EMS Outstation
```

## 目录职责

| 目录 | 职责 | 0.5.1 状态 |
|---|---|---|
| `native/` | C++17 host、后端和原生测试 | TCP、Read/IIN、unsolicited、控制、安全联锁、stats 已接入 |
| `python/` | 可嵌入 pytest 的包与测试 | 类型模型、进程所有权、PICS/点表/场景计划/离线预检/证据/风险门禁和 fixtures |
| `config/` | 框架能力台账和 EMS 配置示例 | 426 条标准/框架能力及独立 PICS、点表、场景计划模板；真实配置不提交 |
| `schemas/` | NDJSON、EMS 配置、本机测试控制和预检报告格式 | v1 严格 Schema |
| `scripts/` | 工具链发现、构建、测试、自检、打包 | Debug/Release/ASan、离线校验和解包回环验收 |
| `third_party/` | 固定源码、归档和锁 | OpenDNP3 3.1.2 及依赖已固定 |
| `docs/standards/` | 标准、PICS、依赖与证据状态 | 缺失项显式保留 |

## 生命周期与并发模型

- 一个 Python client 拥有一个 host 子进程；Windows 下 Job Object 保证父进程消失时回收整个子进程树。
- 一个 host 最多创建一个 Manager、一个 TCP Client Channel 和一个 Master。
- Python 用可重入锁保证同一时刻只有一个 NDJSON 请求在途；Read/命令支持层也拒绝重叠协议任务。
- 断开顺序为：停止/取消命令和 Read -> 取走共享资源 -> Master Disable/Shutdown -> Channel Shutdown -> Manager Shutdown -> 清除安全令牌。
- OpenDNP3 回调只写入有界的任务状态/测量结构并立即返回；不会等待 Python 或 stdout。
- stdout 由主协议线程独占，只输出 JSON；诊断写 stderr。

## Read 数据路径

`integrity_poll`、`class_poll` 和 `read` 为一次性同步任务。每个任务拥有独立 task ID、deadline、IIN 起始序号、接收序号、最多 4096 条分片记录和有界 MeasurementStore；共享 IIN 观测存储最多 1024 条。

detail 模式保留并返回逐点结果；summary 模式只累计计数，不保留或返回逐点记录。超过 `max_measurements`、分片上限或当前任务 IIN 窗口发生丢失时返回 `QUEUE_OVERFLOW`，不会静默丢数据。任务结果同时包含原始/解析 IIN、OpenDNP3 task completion、开始/完成时间和分片摘要；EMS 场景还会拒绝 IIN2.0/2.1/2.2，避免 task success 掩盖请求错误。

当前 master 的自动启动完整性、event scan 和 unsolicited class mask 均关闭，避免建立连接时产生不可控后台任务；调用方必须显式发起 Read 或 `enable_unsolicited`。`OpenDnp3UnsolicitedSupport` 是跨请求存活的 ISOE handler，只收集 unsolicited 回调，使用默认 4096 条的 drop-oldest 队列，并记录会话、分片和接收顺序；disable/断开后的事件不得继续进入队列。原始 Confirm 丢失、序号回绕和重发故障注入仍属于后续互操作任务。

## EMS pytest 场景编排

`ems_test_plan.py` 是 Python/pytest 层的业务编排边界，不修改 NDJSON 或 OpenDNP3 协议层。它在 DUT 连接前加载最多 1 MiB 的严格 JSON，拒绝重复键、未知字段、非有限/越界值、重复场景、无效点引用和不完整恢复闭环，并把每个场景映射为能力矩阵 marker。

公开点表只描述可读对象；场景计划单独描述完整性/Class 读取、外部触发的主动上报期望以及预批准控制闭环。二者在收集阶段交叉校验。`examples/pytest_ems` 的控制测试每次只接受一个命令行精确选择的场景，依次执行基线读回、一次操作、确认后读回、一次恢复和恢复读回；它不会批量选择控制，也不会重试任何控制。如果操作后状态未确认，测试停止且不盲目恢复。

`ems_profile.py` 是 PICS 的唯一严格解析入口，pytest 插件和 `preflight.py` 共用它。离线预检进一步把 PICS、点表、场景计划与能力矩阵交叉计算，在没有 IP/端口且不启动 host 的情况下列出所需能力、blocker、warning 和输入哈希。预检 `READY` 只表示配置门通过，不提升互操作证据等级。

## 本机有状态端到端回归

`dnp3-local-test-outstation.exe` 强制绑定 `127.0.0.1`，提供两个 BI/AI/BOS/AOS 索引。测试控制协议可以显式制造 G2V2/G32V7 事件；G12V1 与 G41V1～V4 命令会改变对应 BOS/AOS 静态反馈，并记录 SBO/Direct/No-Ack 操作计数。

`test_ems_native_scenarios.py` 把公开 EMS pytest 场景模板接到真实 Python client、native host、OpenDNP3 master 和该测试从站，覆盖逐点 Static/Integrity/Class、主动上报以及控制/读回/恢复。该从站与主站仍是同栈，因此只属于本机工程证据。

## 控制数据路径与安全门

Python 仅公开 `CrobCommand` 和四种严格类型的 `AnalogOutputCommand`。命令数组最多 256 点，同类型/同索引重复在进入后端前被拒绝；已实现命令不能通过公共原始 `request()` 绕过类型 API。结果按原请求 ordinal/type/index 关联每个点，summary 和唯一性也做交叉校验。Command Status 经过显式 IEEE 1815-2012 适配，OpenDNP3 的后续版本别名仅作为后端诊断字段保留。固定栈对已识别值保留数值，但会把未知线上值 19～125 折叠为 127；`TIMEOUT`、2012 保留区和 raw 127 歧义均触发事故锁、令牌清除和 host 销毁。

核心状态改变 API 需通过两层门；可复制的 EMS 控制模板再增加两道场景门：

1. pytest 收集阶段要求用例同时带 `dnp3_dut`、能力 ID 和 `dnp3_state_changing`，并得到显式命令行/环境授权及 operator/DUT ID；未标记用例即使整次运行带了解锁参数也只能得到只读连接。
2. `connect` 只有在 `environment=LAB`、`allow_state_change=true` 和两个 ID 均有效时才生成 128-bit 会话令牌；后续每条命令必须携带正确令牌。
3. 私有 `ems_test_plan.local.json` 中的场景必须 `enabled=true`、具有非占位授权引用，并定义同类型/同索引的恢复命令和等于初始基线的恢复期望。
4. 每次 pytest 调用必须显式传 `--dnp3-control-scenario <精确ID>`；该选择故意没有环境变量回退，且一次只能选择一个。已授权状态改变测试会拒绝 pytest-xdist；控制模板还记录本进程已尝试的场景并拒绝 rerun/repeat。

Python 客户端不公开令牌属性，只在内存中自动附加；高层连接结果和诊断会移除/过滤令牌，断开或进程退出后销毁。该机制只防误操作，不是认证/授权/SAv5。

状态改变还要求持久 `SafetyIncidentStore`。控制响应超时、host 交换失败、提交异常、结果错配/损坏、结果自报不确定，或点状态无法证明确定拒绝时，客户端把不含控制值/DUT 明文的事故锁写入 `active/<dut-sha256>.json`，清除令牌并销毁 host。后续进程先查锁，锁存在或损坏均 fail-closed；只读路径不受影响。独立读回和明确确认后，记录转入 `archive/`。该层解决同一持久目录上的跨进程误重试，不提供多机分布式锁或身份认证。

## 能力与证据模型

`config/capability_matrix.csv` 是“标准要求/框架实现状态”的唯一台账，其中 `dut_pics_status` 必须保持 `UNKNOWN`；它不能冒充某一台 EMS 的 PICS。每台 DUT 的 `SUPPORTED/NOT_SUPPORTED/UNKNOWN` 单独保存在未提交的 `ems.local.json`，运行时再叠加到框架台账。对象目录只能证明目录覆盖，完整标准声明还需要逐条 requirement catalog（shall/shall not/conditional）及可追踪测试。`hello.capabilities` 只暴露当前实现的子集，并带实现 revision 和本机验证范围。当前本机端到端从站也使用 OpenDNP3 3.1.2，因此相关能力保持 `IMPLEMENTED_UNVERIFIED`；独立端/真实 EMS 证据齐全后才能升级。

构建时 `build-info.json` 固定 host 版本、Git commit、工作区 clean/dirty/unavailable 状态、OpenDNP3 commit、构建配置、目标架构、依赖锁哈希和能力矩阵哈希。pytest `EvidenceRecorder` 为每次运行原子生成脱敏 manifest/结果，私有 PICS、点表和场景计划只记文件名、大小和 SHA-256，已知项目/测试/host/输入/证据路径会替换为占位符。能力行升级到任一 `VERIFIED_*` 时，`evidence` 引用必须追加实际文件的 `#sha256=<64 hex>`，验证器会读取文件复算；仅有可变路径不能作为可审计证据。正式证据应使用 `git_worktree_state=clean` 的构建并保存这些文件，不应只记录 EXE 文件名；任意测试输出仍需人工审查后才能外发。

发布脚本把安装树写入 `package-manifest.json`，再用固定时间戳、排序条目和固定压缩参数生成 ZIP/SHA-256；验证阶段在新目录解包、逐文件校验并执行真实本机回环。它保证相同安装树的 ZIP 字节稳定，但不声称 MSVC 输出本身已达到跨机器可复现。

## 后续扩展顺序

T12 的受控 unsolicited 基线已完成；后续按独立任务增加真实 EMS/原始故障时序、时间同步、Restart/Freeze/Assign Class、持续 capture/性能、其他承载、经典对象缺口、高级事务和安全功能。不得扩大单会话/单在途 RPC 边界，除非有独立设计与迁移任务。

具体阻塞、输入和验收见 `docs/INTRANET_HANDOFF_REMAINING_TASKS.md`。
