# 版本变更记录

本文件记录用户可见行为和移植边界的变化。能力是否适用于某台 EMS，仍以该设备的正式 PICS、私有配置和不可变测试证据为准；版本记录不能替代互操作或一致性结论。

## 未发布修复（2026-09-22）

- 补齐 Git 曾漏收的 OpenDNP3 上游 `dotnet/nuget/build/opendnp3.props` 和
  `opendnp3.targets`；二者已与锁定归档核对，未修改第三方内容或依赖锁。
  根 `.gitignore` 仅为这两个源码输入增加例外，其他构建输出仍忽略。
- 增加 Git 源码交付完整性回归，防止工作区存在但未跟踪的文件掩盖漏提交。
  修复干净克隆在 `doctor.ps1` 中报 `MISSING_SOURCE_FILE` 的问题。

## 未发布更新（2026-09-21）

- 新增显式 `trace.start/read/stop`、Python 双向报文重组/解码与完整性诊断。
  默认关闭，观察边界是栈已编码 TX/链路校验通过 RX，不代替网卡抓包；详见
  `docs/PROTOCOL_TRACE.md`。
- 修复 capture worker 早于成员初始化启动的竞态；补反复构造、活动中销毁和排空测试。
- 读任务在锁内固定超时判定，迟到完成不能将旧 IIN 快照包装成成功；补确定性回归。
- LAB 事故记录改为有界直接读取，仅明确不存在视为无锁；权限、格式、读取及
  失效链接错误均保持锁定。SIMULATOR 仍不访问事故存储。
- 响应 JSON 限制 64 层，解析/复制递归失败统一按协议错误处理；控制可能已发出时
  清令牌并销毁会话，LAB 留事故、SIMULATOR 可用新 client 继续，无自动重试。
- 启动中断覆盖 Job 分配、部分 IO 线程启动及返回结果阶段，先回收资源再传播中断。
- pytest 证据新增运行内唯一 `case_id`，不再合并脱敏/截短后同名的参数化用例。
- 离线完整性检查补齐 OpenDNP3 本体、nlohmann/json 和已锁许可证，不修改锁哈希
  或第三方源码；文本仅允许 CRLF/LF 换行等价。
- 迁移验收独立、有界排空 stdout/stderr，依据退出码判断成功，修复 PowerShell
  5.1 对普通 stderr 警告的误报；JSON 探针仅解析 stdout。

## 未发布修复（2026-09-07）

- 新增单文件模拟器接入配置和 `examples/pytest_simulator`：host 自动发现/配套校验、
  分层诊断、16 个示例参数化用例、精确空 Class 响应、控制反馈及外部事件变化观察。
  新增配置 Schema、移植入门文档及复制目录后的真实本机回环回归。
  EMS 为监听端；不开发时间同步、Restart 或外部模拟器触发适配器。
- 文档进一步区分 LAB 与 SIMULATOR 的授权/事故处理，补充 host 自动定位和错误分层
  的用途、GitHub 源码与运行包的区别，并在内网任务卡正文排除当前不需要的能力。

- 新增显式 SIMULATOR 模式：pytest ini/命令行/环境变量和直接 API 均可启用；
  无需 operator/DUT ID、审批、持久事故锁和逐次场景选择，支持批量与重复控制。
  SIMULATOR 计划的前置条件/恢复动作可选，命令结果和反馈仍校验。模拟器 fixture
  每条测试使用独立 host，失败后下一条可继续；证据标明模拟器环境。
- LAB 控制请求可能发出后遭遇 `KeyboardInterrupt` / `SystemExit`，包括等待和结果
  校验阶段，均先记录持久事故锁、清除令牌并销毁会话，然后传播中断；发出前
  的本地校验失败不创建事故。控制结果处理与发包使用同一串行临界区。
- Python 使用单个有界 stdin writer，写入与响应等待共用单调时钟截止时间；
  host 不读输入时也能超时回收。新增 `HostProcessConfig.max_request_bytes`
  （默认 1 MiB，64 B～16 MiB），过大请求在入队前拒绝。
- 发布源码改由 `scripts/package-source-files.json` 逐文件选择，忽略未列入清单的
  本地文件；打包前再次检查完整文件集合，拒绝额外文件和符号链接/reparse point。
- soak 达到目标时长后仍核验停止状态、host 存活、通道 OPEN 和完整连接事件，
  并采集结束资源样本，防止最后一次等待期间的退出/中断被误报为通过。

## 0.6.1（2026-08-31）

### 干净发布与迁移验收

- 新增 fail-closed `release.ps1`：只接受当前 clean commit，核对 build-info，运行
  Release 全量回归与 1,000 次生命周期，从同一构建连续打包两次并要求 ZIP 哈希
  一致，最后生成发布闭环报告；普通 `package.ps1` 也拒绝 dirty、stale、Debug 或
  非 x64 构建，显式非 clean 开关只允许本地检查。
- 可移植包新增固定时间戳生成的 `py3-none-any` wheel、`compatibility-test.ps1` 和
  空白 pytest consumer。迁移验收按每个实际解释器验证清单、回环、无网络隔离安装、
  导入来源、版本一致性、插件参数与安全默认值，且不改写发布包或目标虚拟环境。
- 新增严格兼容性/发布闭环报告 Schema；报告固定声明仅为本机包与同栈回环证据，
  不形成真实 DUT、独立互操作或 IEEE 一致性结论。未实际运行的 Python/pytest
  组合继续记为未覆盖。

## 0.6.0（2026-08-31）

### 持续 Capture

- 新增类型化 `capture.begin/progress/end`，支持 `static_set`、
  `event_sequence` 和 `observation` 三种模式；每个会话最多一个 ACTIVE capture。
- 静态点集报告 missing/duplicate/unmatched，确定性事件流使用外部 manifest、连续
  sequence 和有序 SHA-256 真值；无外部真值时完整性明确为 unknown。
- 队列、范围、点数、异常样本、duration 和响应均有硬上限；deadline、断开、
  shutdown、错 ID、并发 begin 与 overflow 都有稳定终态和 native/Python/回环测试。

### 性能、大点表与稳定性

- 本地参考从站扩展到每种类型 65,535 点，并新增基于 seed/sequence 的 BI/AI
  突发与定速事件发生器及可复算 manifest；定速模式使用单调时钟绝对 deadline
  逐事件 Apply，避免 Windows 相对 sleep 漂移。
- 本机边界回归实际覆盖 BI/AI/BOS/AOS 各 4,096 点（总计 16,384 点），以及
  4,096 条 burst 和 1,000 events/s 的 4,096 条 paced event chunk；公共示例
  Profile 保持较小默认规模，不代表工具已验证上限。
- 新增严格性能 Profile/报告 Schema、输入 SHA-256、按 kind 和 Group/Variation 的
  精确对象真值、nearest-rank p50/p95/p99/max、capture A/B 开销和阈值判定。
- 新增 Windows host CPU、工作集、private bytes、句柄和线程采样；网络字节、
  DUT 资源及其他无可靠数据源的指标保持 `null`，不会估算。
- 新增只读可中断 soak runner，包含 watchdog、原子检查点与轮转、磁盘/证据上限、
  资源增长、无丢失 channel-event 重连审计和连续失败门禁；事件基准还会清空并
  核验固定 4,096 条 master unsolicited 队列。CI 只跑缩短场景；本版本没有声称已完成真实 EMS
  或正式 24 小时环境验证。

### pytest、移植与文档

- 新增可复制的 `examples/pytest_performance`；benchmark 默认有界，24 小时运行必须
  显式传入 `--dnp3-run-soak`，且仅允许只读任务。
- 性能/事件 Profile 在 pytest 收集阶段严格加载并哈希，场景按实际 FC、对象与
  Qualifier 能力 ID 受 PICS 和公共能力矩阵共同门控。
- 可移植包加入性能/事件 Profile、报告 Schema、性能 pytest 套件和
  `PERFORMANCE_AND_SOAK_GUIDE.md`；包内 self-test 还会对 BI/AI/BOS/AOS 共 8 点
  执行精确静态 capture 并核验无缺失、重复、意外点或 overflow；版本提升为 0.6.0。
### 证据边界

- H08a/T15 与 H09a/T16a～T16d 的本机工具基线已完成；H08b 的目标 Profile 映射和
  H09b/T16e 的独立参考端、目标 EMS、PCAP、DUT 资源及正式 24 小时证据仍留在内网。
- 同栈回环和短时 soak 只能证明工具行为，相关能力继续保持
  `IMPLEMENTED_UNVERIFIED`。

## 0.5.1（2026-08-31）

### 安全与控制

- 控制超时、host 交换/提交失败、结果结构或请求关联异常，以及点级 `TIMEOUT`、IEEE 1815-2012 保留状态或 decoded 127 歧义，统一按“不确定状态改变”处理。
- 不确定结果会先写入 DUT 专属持久事故锁，再清除安全令牌并销毁 host；禁止自动重试，必须由新会话独立读回并显式确认。
- 公共 `request()` 不再允许调用已有类型化 API 的命令，避免绕过客户端状态、结果校验和事故锁。

### Read、IIN 与资源完整性

- Read 结果增加严格跨字段校验；测量、分片或当前任务 IIN 观测丢失均返回 `QUEUE_OVERFLOW`，不会以不完整数据成功。
- 分片记录固定最多 4096 条，共享 IIN 观测固定最多 1024 条；`summary` 只降低明细内存/JSON，不取消完整性上限。
- EMS pytest 场景即使收到 OpenDNP3 task `SUCCESS`，仍会拒绝 IIN2.0 `NO_FUNC_CODE_SUPPORT`、IIN2.1 `OBJECT_UNKNOWN` 和 IIN2.2 `PARAMETER_ERROR`。

### PICS、Qualifier 与构建一致性

- pytest 将私有 PICS 与公共框架状态双重门控；DUT 的 `SUPPORTED` 不能覆盖框架 `BLOCKED` 或 `UNSUPPORTED_BY_BACKEND`。
- 场景依赖加入 Q00/Q01/Q06/Q17/Q28；固定 OpenDNP3 3.1.2 无法表达的 Q02/Q09/Q39 保持 `UNSUPPORTED_BY_BACKEND`，不静默降级。
- Python/host 版本、OpenDNP3 版本和能力矩阵 SHA-256 在启动/收集阶段交叉校验，避免混用旧 EXE、Python 包或矩阵。

### 文档与移植

- 修正离线预检、本机从站和内网交接命令的 PowerShell 路径及 EXE 环境前提。
- 统一使用 `RESPONSE_TIMEOUT` 和 `config/ems.local.json`，并更新 hello、能力 ID、IIN、unsolicited、生命周期和溢出排错示例。
- 可移植包继续包含本机回环工具、严格 EMS pytest 场景、Schema、能力矩阵和离线预检，但不包含 IEEE 标准 PDF、私有 PICS/点表/计划、PCAP 或密钥。

### 证据边界

- 本版本的端到端自动回归两端仍使用 OpenDNP3 3.1.2，只能证明本机工程调用链；相关能力保持 `IMPLEMENTED_UNVERIFIED`。
- 真实 EMS、独立实现、原始 Confirm/重发故障时序、H08 capture、H09 性能/大点表/24 小时结论仍按内网任务卡推进。
