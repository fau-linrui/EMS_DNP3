# EMS_DNP3 内网交接与剩余任务卡

本文是给项目负责人和内网 agent 的执行清单。它只描述当前仓库真实状态，不把本机自测写成 EMS 互操作结论。每次对内网 agent 只下发一个任务卡；完成、评审、提交后再下发下一张。

## 1. 当前已经完成什么

截至 0.5.1，仓库已完成指导书 T00～T12 中可在本机可靠闭环的核心部分：

- Windows x64 CMake/Visual Studio 工程、固定 OpenDNP3 3.1.2 和全部离线构建依赖。
- C++ host 的严格 NDJSON 协议、Schema、错误码、重复请求 ID 防护、请求大小/深度限制和有序清理。
- Python 子进程客户端、Windows Job Object、超时/异常退出诊断、pytest 插件和 1,000 次生命周期测试入口。
- TCP Client 单会话连接/断开、连接超时、退避重连和有界通道事件队列。
- `integrity_poll`、Class 1/2/3 Poll、严格范围/计数/多 Header `read`。
- EMS 约定中的 G1V2/G2V2、G30V5/G32V7、G10V2、G40V3 和 G60V1～V4 已有精确本机对象覆盖。
- OpenDNP3 公开 SOE 回调中的 BI、DBBI、BOS、Counter、Frozen Counter、Analog、AOS、Octet String、Time-and-Interval 等类型归一化；保留索引、原始 flags、时间、接收顺序、分片摘要和有界结果。
- 原始两字节 IIN 和解析位；已覆盖 IIN2.1 `OBJECT_UNKNOWN`。EMS 场景会拒绝 IIN2.0/2.1/2.2，不能因 OpenDNP3 task `SUCCESS` 或允许空响应而误通过。
- 读任务开始/完成、状态、耗时、失败详情、取消和溢出失败模型；Measurement、最多 4096 条分片记录和最多 1024 条 IIN 观测均有界，当前任务窗口发生丢失即 `QUEUE_OVERFLOW`。
- CROB 的 Select-Before-Operate 和有响应 Direct Operate。
- Group 41 的 int16、int32、float32、double64 四种 Analog Output，以及混合批次的逐点结果。Command Status 已按 IEEE 1815-2012 Table 11-7 显式适配，同时保留后端解码值、2012 保留区标记、OpenDNP3 别名和原始线上值歧义标志；19～125 被固定栈折叠成 127 的限制没有被伪装成无损支持。
- 控制默认锁定；只有显式 LAB、允许状态改变、operator ID 和 DUT ID 同时存在时才生成一次性会话令牌。Python 不公开/回显令牌，诊断会脱敏，令牌在断开/退出时失效。
- FC20/FC21 显式 Enable/Disable Unsolicited、跨请求 ISOE 收集、G2V2/G32V7、会话/分片/顺序字段及 4096 条 drop-oldest 有界队列；启动时仍默认关闭。
- 控制超时、host 交换/提交失败、非法或错配控制结果、结果自报不确定，或点级 `TIMEOUT`、2012 保留状态、decoded 127 歧义时，框架不自动重试；Python 会先按 DUT 哈希写入跨进程事故锁，再清除令牌并终止 host。新进程只读可用，控制必须在独立读回和显式事故确认后恢复。
- `DIRECT_OPERATE_NR` 明确返回 `UNSUPPORTED_BY_BACKEND`，不会模拟成功。
- PICS 三态门禁、能力 ID 与 426 行公共框架矩阵交叉校验、严格只读点表加载器、严格 EMS 场景计划，以及 `dnp3_dut`、`dnp3_unsupported_behavior`、`dnp3_state_changing` 风险门禁。PICS 的 `SUPPORTED` 不能覆盖框架 `BLOCKED`/`UNSUPPORTED_BY_BACKEND`；场景依赖已包含 Q00/Q01/Q06/Q17/Q28，Q02/Q09/Q39 明确受固定后端限制。公共矩阵的 `dut_pics_status` 固定为 `UNKNOWN`，真实设备状态只存在于私有 PICS overlay。
- 可整体复制的真实 EMS pytest 套件：逐点 Static Read、完整性/Class Poll、外部触发的 unsolicited 精确匹配，以及“前读回 -> 单次控制 -> 后读回 -> 单次恢复 -> 恢复读回”模板。主动上报和控制默认关闭；控制还必须逐次精确选择一个场景。
- 只绑定 `127.0.0.1` 的可编程有状态测试从站：可制造带时间的 BI/AI 事件，CROB/G41 会更新 BOS/AOS 反馈，并记录 SBO/Direct/No-Ack 操作次数。真实 EMS 场景模板已通过 Python -> native host -> OpenDNP3 -> 测试从站的完整链路回归。
- 共用严格 PICS 模型的离线预检：在不启动 host、不连接 DUT 的情况下交叉检查 PICS、点表、场景计划和能力矩阵，输出逐能力 blocker、输入 SHA-256、JSON 报告和明确退出码。
- pytest 脱敏证据记录器：运行/测试阶段结果、构建身份及 PICS/点表/场景计划/矩阵文件名、大小、SHA-256；不复制私有输入内容，并替换已知本机绝对路径。任意 DUT/第三方输出仍须在外发前人工复核。
- 有界 `stats`、环境体检、确定性 ZIP/SHA-256/逐文件清单、解包校验和不接真实 EMS 的本机一键读写自检。
- `build-info.json` 记录 Git commit 和 clean/dirty/unavailable 工作区状态；正式证据只接受 clean 构建。
- Python 启动握手强制校验 0.5.1 host 版本；pytest 还校验当前能力矩阵 SHA-256。已实现命令禁止从公共原始 `request()` 绕过类型 API，避免安全令牌或客户端会话状态失步。
- 426 行 IEEE 1815-2012 能力矩阵；已补齐 Table 4-6 的 22 个限定词代码、2012 精确 IIN/Command Status 名称、遗漏的 obsolete-but-assigned Counter 变体和 G70V0，并单列 OpenDNP3 对保留 Command Status 原始线上值的折叠缺口。已实现项均保持 `IMPLEMENTED_UNVERIFIED`，没有虚构 `VERIFIED_INTEROP/CONFORMANCE`。
- 已把用户提供的“DNP3 操作约定”登记为部分输入，并在 `docs/standards/ems_device_profile.md` 记录 D01～D09；由于缺正式 PICS/固件身份且文档内部有冲突，DUT 状态仍全部为 `UNKNOWN`。

主要实现入口：

```text
native/src/HostController.cpp
native/src/OpenDnp3Backend.cpp
native/src/OpenDnp3ReadSupport.cpp
native/src/OpenDnp3UnsolicitedSupport.cpp
native/src/OpenDnp3CommandSupport.cpp
python/src/dnp3_master/client.py
python/src/dnp3_master/models.py
python/src/dnp3_master/pytest_plugin.py
python/src/dnp3_master/ems_profile.py
python/src/dnp3_master/point_table.py
python/src/dnp3_master/ems_test_plan.py
python/src/dnp3_master/preflight.py
python/src/dnp3_master/local_outstation.py
python/src/dnp3_master/evidence.py
python/src/dnp3_master/safety_incidents.py
native/tests/opendnp3_read_integration_tests.cpp
native/tests/local_outstation_main.cpp
python/tests/test_host_read.py
python/tests/test_ems_native_scenarios.py
config/capability_matrix.csv
config/ems_test_plan.example.json
examples/pytest_ems/
```

## 2. 证据边界：交接时必须原样保留

当前 Read 和控制端到端测试的两端都使用 OpenDNP3 3.1.2，并且只运行在本机回环网络。因此它们能证明框架调用链、数据模型、安全门和资源清理，但不能证明：

- 与真实 EMS 互操作；
- 与非 OpenDNP3 独立实现互操作；
- 对 IEEE 1815-2012 的一致性；
- 特定 EMS 固件的 PICS 能力；
- 生产网络的性能、稳定性或安全性。

内网 agent 不得只因测试通过就把能力矩阵升级为 `VERIFIED_INTEROP` 或 `VERIFIED_CONFORMANCE`。升级状态至少需要不可变的运行清单、构建信息、配置哈希、独立端版本、PCAP/报告哈希和评审人；矩阵 `evidence` 必须使用 `relative/path#sha256=<64 hex>`，校验器会读取文件复算。

本机 IEEE PDF 带第三方许可水印，仅用于本地条款复核，已被 Git 忽略；不得复制到发布包、GitHub、内网代码仓库或 agent 提示词附件。仓库只允许保存条款号、能力摘要和文件哈希。

## 3. 当前已知限制

- 只支持 Windows x64 和 TCP Client；没有 TCP Server、Serial、UDP、TLS。
- 一个 `Dnp3MasterClient`/host 进程只拥有一个 DNP3 Master 会话和一个在途 RPC；不支持并发任务。
- 没有周期扫描调度器。Class Poll 和 Read 都是调用方显式发起的一次性任务。
- 已有显式、持久、有界 unsolicited 收集器，但启动时不自动扫描/启用；尚无 Confirm 丢失、应用序号回绕、重发/重复和 solicited 交错的原始帧故障注入证据。
- 没有 `capture.begin/progress/end`、PCAP 采集、黄金字节播放器、Raw frame 注入或独立一致性工具适配。pytest 证据清单不能替代 PCAP 或签名归档。
- `stats` 只报告 host 请求计数、会话/通道/队列摘要；没有链路字节、网络字节、首字节/首对象时间、CPU、工作集、private bytes、句柄和线程历史。
- `summary` 是单次 Read 的有界汇总，不是跨任务的持续 MeasurementStore。
- 控制只支持 OpenDNP3 公共 API 可表达的有响应 SBO/Direct Operate；`DIRECT_OPERATE_NR` 不支持。
- Read/命令公共 API 只支持 Q00/Q01/Q06/Q07/Q08/Q17/Q28 所覆盖的 8/16-bit 路径；Q02/Q09/Q39 的 32-bit qualifier 需要后端扩展或更换协议栈。
- OpenDNP3 3.1.2 会把未识别的 Command Status 线上值 19～125 折叠成 127；框架会报告 `status_wire_raw_unambiguous=false`，但没有 raw decoder 前无法恢复真实字节。
- 控制会话令牌只用于防误操作，不是用户认证、权限系统或 Secure Authentication v5。
- 事故锁只对使用同一持久目录和完全相同 `dut_id` 的进程有效，不是多机分布式锁；真实控制必须保持单控制器、串行执行。
- 未完成时间同步、Restart、Freeze、Assign Class、File/Data Set/VT、SAv5、Raw fault/fuzz 等能力。
- 已有真实 EMS 点表/场景断言模板，但尚无目标 EMS 的实际运行结果、PICS 适用性结论、性能阈值、24 小时稳定性或独立互操作证据。
- 已知 EMS 操作约定声称事件仅 unsolicited、支持 FC5/FC6、使用“Activation Model”式 LATCH_ON/OFF、TCP 不支持广播且遥脉同遥测；这些陈述存在标准差异/歧义，不能直接转成自动化断言。
- 426 行矩阵是能力/对象目录，不是逐条 `shall/shall not/conditional` 的完整规范要求目录；在完成 requirement catalog 和双人标准复核前，不得宣称 IEEE 1815-2012 规范要求全覆盖。

## 4. 进入内网后必须取得的输入

在这些输入齐全之前，agent 只能做本机开发，不能形成最终 EMS 结论：

1. EMS 厂商、型号、固件、Device Profile/PICS 版本及其批准来源。
2. 连接角色和参数：EMS 是否为 Outstation/TCP Server、IP、端口、本地网卡、主/从链路地址、最大分片和超时要求。
3. 点表及场景计划输入：对象类型、索引、Class、量程、工程单位、初值、可写性、控制反馈点、操作/恢复值、危险等级和批准工单。
4. 主动上送、启动完整性、Confirm、时间同步、Restart、Freeze、Assign Class 策略。
5. 隔离实验环境的书面状态改变授权、操作人标识、资产标识、回退步骤和允许时段。
6. 目标点数、事件率、延迟、吞吐、CPU/内存和稳定性阈值。
7. 至少一个非本项目/非同一 OpenDNP3 构建的独立实现或批准的一致性工具。
8. PCAP、报告、构建产物和运行证据的内部保存位置、访问控制和保留期限。
9. IEEE 1815-2012 PDF 的内部使用授权、对应勘误和条款复核责任人。
10. 对 `docs/standards/ems_device_profile.md` 中 D01～D09 的厂商书面答复，尤其是 FC6 无响应、SBO、事件 Read、CROB 点模型、广播和遥脉对象映射。

私有输入放在 `config/*.local.*`、`secrets/` 或 `evidence/local/`，包括 `ems.local.json`、`points.local.csv` 和 `ems_test_plan.local.json`，不得提交到 GitHub。

## 5. 给内网 agent 的固定执行规则

每次任务开始时要求 agent 重新读取：本文件、开发指导书、当前任务涉及的能力矩阵行、`git diff`、固定 OpenDNP3 3.1.2 头文件和相关测试。不要依赖上一次聊天记忆。

每张任务卡必须遵守：

1. 只完成一个主要结果，不顺手实现下一卡或重构无关代码。
2. 先写/更新失败测试，再做最小实现；禁止只改文档或返回固定假数据。
3. 不猜 OpenDNP3 API；从 `third_party/opendnp3` 固定源码确认类型、生命周期和缺口。
4. 所有输入严格校验、队列/数组/文本有上限、超时路径可取消、断开后无悬挂回调。
5. stdout 只能输出 NDJSON；日志只能去 stderr，且不得包含令牌、密钥或私有配置。
6. 未支持能力返回稳定错误码，不能返回空成功。
7. 控制、时间写入、Restart、Freeze、Assign Class、文件/配置和 fuzz 必须有相应风险标记；未经授权绝不连接生产设备。
8. 每卡结束运行 Release 全套测试，更新能力矩阵、Schema、`hello.capabilities` 和文档，再提交一个独立 commit。
9. 不确定控制结果必须按 `docs/SAFETY_INCIDENT_RUNBOOK.md` 只读核对和确认；禁止删除/编辑活动锁，禁止自动重发。
10. 真实 DUT 运行必须指定 `--dnp3-evidence-dir`；私有 PICS/点表/场景计划只保存哈希，不复制进公开仓库。自动脱敏不是数据泄露审查的替代品，证据出内网前必须人工复核。

通用本机验收命令：

```powershell
.\scripts\doctor.ps1
.\scripts\build.ps1 -Preset windows-msvc-release
.\scripts\test.ps1 -Preset windows-msvc-release
.\scripts\run-local-self-test.ps1 -Preset windows-msvc-release
git diff --check
git status --short
```

`test.ps1` 已设置 `DNP3_MASTER_HOST_EXE`/`DNP3_TEST_OUTSTATION_EXE` 并包含 `python\tests\test_ems_native_scenarios.py`；不要在未设置这两个变量时把该文件单独追加到命令清单。需要定向运行时，按 `docs/LOCAL_TEST_OUTSTATION.md` 的环境保存/恢复示例执行。

## 6. 剩余任务卡

### H00（P0，可在无 DUT 环境继续）：逐条规范要求目录

唯一目标：基于经授权的 IEEE 1815-2012 文本建立独立 requirement catalog，不修改线上协议行为，也不把能力目录行数当作标准要求覆盖率。

要求：每条 `shall`、`shall not` 和条件性要求具有稳定 requirement ID、准确条款/表/图、主站适用性、前置条件、关联 capability ID、实现状态、测试 ID、证据和复核人；废止、保留、条件性及“不适用于主站”均需依据，不能直接删除。先定义 CSV/JSON Schema 和校验器，再分章导入并由第二人复核。受限标准原文不得复制进仓库，只保存必要摘要和条款定位。

验收：目录去重/引用/枚举/条件闭环校验通过；每个 requirement 能追踪到能力或有正式不适用依据；覆盖率报告分别显示“能力目录”“规范要求”“框架实现”“DUT 适用项”，不合并成一个百分比。

停止条件：标准授权、勘误版本或复核负责人不明确。此任务可以与 H01 和 H08a 并行，但在完成前不能使用“全标准要求已覆盖”的发布措辞。

### H01（P0）：导入并冻结 EMS PICS/连接基线

唯一目标：把获批 EMS Profile 转成机器可读本地配置，不写协议功能。

先读：`docs/standards/ems_device_profile.md`、`docs/OFFLINE_PREFLIGHT.md`、`config/ems_profile.example.json`、`config/points.example.csv`、`config/ems_test_plan.example.json`、对应 Schema、`python/src/dnp3_master/ems_profile.py`、`preflight.py`、`pytest_plugin.py`、`point_table.py`、`ems_test_plan.py` 和 `config/capability_matrix.csv`。

操作：

- 复制示例为 `config/ems.local.json`；填写准确设备身份和 Profile revision。
- 每项只按原始 PICS/Device Profile 填 `SUPPORTED`、`NOT_SUPPORTED` 或 `UNKNOWN`。
- 复制严格模板为 `config/points.local.csv`，只填写模板已有的只读点定义列；不得增删列。控制反馈、批准值和风险等级另存内部受控清单，不交给通用只读加载器。
- 复制场景模板为 `config/ems_test_plan.local.json`；填写只读 poll 期望。主动上报和控制保持关闭，直到对应任务卡的输入和授权齐全。
- 记录原始文档哈希和批准人；原文存内部文档系统，不提交仓库。
- 逐项关闭 D01～D09；厂商未答复或答复仍冲突的能力保持 `UNKNOWN`，不能用现场试错替代书面确认。

安全验证：

```powershell
.\.venv\Scripts\python.exe -m dnp3_master.preflight `
  --pics config\ems.local.json `
  --points config\points.local.csv `
  --plan config\ems_test_plan.local.json `
  --capability-matrix config\capability_matrix.csv `
  --json

.\.venv\Scripts\python.exe -m pytest examples\pytest_ems --collect-only `
  --dnp3-pics-file config\ems.local.json `
  --dnp3-points-file config\points.local.csv `
  --dnp3-ems-plan config\ems_test_plan.local.json `
  --dnp3-unknown-policy error
git status --short
```

验收：预检退出码为 0 且报告 `scope=OFFLINE_CONFIGURATION_ONLY`、无 blocker；JSON 无重复键/未知字段/非法状态；点表和场景引用交叉校验通过；每个能力 ID 都能在 `config/capability_matrix.csv` 中找到；所有计划执行能力不再是 `UNKNOWN`；三个本地配置均不出现在 Git 状态中。预检通过不是 EMS 互操作证据。

停止条件：Profile 与实际固件不匹配、无版本/批准来源、连接角色或链路地址不确定，或 D01～D09 未关闭。此时保持 `UNKNOWN`，向 DUT 负责人提问。

### H02（P0）：真实 EMS 只读冒烟与点表断言

唯一目标：在隔离 EMS 上完成连接、总召、单点范围、多 Header、Class Read 的只读基线。对于“事件 Read 恒为空”的厂商约定，应分别保存无事件和人工产生已知事件时的结果，不能把任意空响应当成功。

先读：`docs/BEGINNER_MIGRATION_BUILD_USE_GUIDE.md`、`examples/pytest_ems/README.md`、Read 模型/客户端、PICS、本地点表和本地场景计划。

修改范围：0.5.1 已提供 `test_read_points.py` 和 `test_poll_scenarios.py`。先只填写私有点表/场景计划并运行，不改协议或控制代码；只有业务断言确实缺失时，才在内网复制目录中做最小扩展。

每个真实 DUT 用例必须同时带：

```text
@pytest.mark.dnp3_dut
@pytest.mark.dnp3_capability("对应能力ID")
```

第一次运行只收集，然后串行执行；命令中绝不加入状态改变开关：

```powershell
.\.venv\Scripts\python.exe -m pytest examples\pytest_ems --collect-only `
  --dnp3-pics-file config\ems.local.json `
  --dnp3-points-file config\points.local.csv `
  --dnp3-ems-plan config\ems_test_plan.local.json `
  --dnp3-unknown-policy error

.\.venv\Scripts\python.exe -m pytest `
  examples\pytest_ems\test_read_points.py `
  examples\pytest_ems\test_poll_scenarios.py -v -ra `
  --dnp3-host-exe out\build\windows-msvc-release\bin\dnp3-master-host.exe `
  --dnp3-pics-file config\ems.local.json --dnp3-points-file config\points.local.csv `
  --dnp3-ems-plan config\ems_test_plan.local.json `
  --dnp3-evidence-dir evidence\local --dnp3-unknown-policy error `
  --dnp3-outstation-host "<LAB_EMS_IP>" --dnp3-outstation-port <PORT> `
  --dnp3-master-address <MASTER_ADDR> --dnp3-outstation-address <OUTSTATION_ADDR>
```

验收：固定点的类型/索引/flags/值范围符合点表；原始/解析 IIN 保存；空响应、OBJECT_UNKNOWN、断线中断和重新连接有明确断言；报告包含 `build-info.json` 和本地配置哈希。

停止条件：任何地址不确定、EMS 不是测试环境、结果出现未知写操作、点表与响应明显不一致或 `QUEUE_OVERFLOW`。停止后保存诊断，不扩大范围。

### H03（P0）：独立互操作和不可变证据

唯一目标：用非本项目/非同一 OpenDNP3 构建的参考端重复 H02，并建立可审计证据。

先读：开发指导书 M2/M9、能力矩阵、内部证据保留规则。

输出至少包括：pytest 自动生成的 `manifest.json`/`pytest-results.json`、参考端产品/版本/配置、host `build-info.json`、Git commit、PICS/点表哈希、开始结束时间、测试 ID 结果、PCAP 哈希、日志哈希、环境/网卡说明和评审人。

验收：Read、Class、对象类型、IIN、多分片、断线中断在独立端通过；同一 run ID 可定位全部证据。只有证据齐全的能力才可从 `IMPLEMENTED_UNVERIFIED` 提升为 `VERIFIED_INTEROP`。

停止条件：参考端实际仍使用相同 OpenDNP3 代码、无法抓包/存证、版本不可确定或证据包含未经批准的敏感信息。

### H04（P1，仅授权后）：真实 EMS 控制验证

唯一目标：对获批低风险点验证 CROB/Analog Output 的 SBO、Direct Operate、逐点状态和读回。

先读：PICS、批准点表/工单、回退方案、`examples/pytest_ems/README.md`、`ems_test_plan.py`、`test_control_scenarios.py`、`OpenDnp3CommandSupport.cpp`、Python 命令模型和开发指导书 4.4.4/4.4.5 对应内部条款索引。

0.5.1 已提供单场景控制闭环模板，并已用本机有状态反馈从站验证完整调用链；不要先重写控制代码。把准确操作/恢复、反馈期望和真实 `authorization_reference` 写入私有计划，只启用本次获批场景。模板会自动附加 `dnp3_dut`、准确 capability ID 和 `dnp3_state_changing`。运行时必须同时提供：

```text
--dnp3-control-scenario "<EXACT_ENABLED_SCENARIO_ID>" `
--dnp3-allow-state-changing `
--dnp3-operator-id "<OPERATOR_OR_TICKET>" `
--dnp3-dut-id "<LAB_ASSET_ID>"
```

还必须传 `--dnp3-ems-plan config\ems_test_plan.local.json`、PICS、点表、持久证据/事故目录和全部连接参数，并只运行 `examples\pytest_ems\test_control_scenarios.py`。选择参数没有环境变量替代，一次只能选择一个场景；框架会拒绝已授权的 xdist 和同进程 rerun/repeat，实验环境仍须保证没有第二个控制器。

验收顺序：先由厂商确认每点是 Activation、Complementary Latch 还是 Complementary Two-output 模型，并确认 FC3/FC4；随后由现有模板完成操作前读回 -> 发一个命令 -> 检查每点 Command Status -> 操作后读回/业务反馈 -> 发一个批准的恢复命令 -> 恢复读回。只有后状态确认后才自动恢复；状态不明时停止并人工处置。拒绝状态、SBO 超时/不匹配和混合批次部分失败应另建独立、经评审的负向任务，不能混进首次低风险控制；控制超时不自动重试。

`DIRECT_OPERATE_NR` 保持 `UNSUPPORTED_BY_BACKEND`，除非另立 M5 扩展任务并有抓包、副作用读回和独立端证据。

停止条件：生产设备、无书面授权、无回退、点号/值不确定、CROB 点模型或 FC3/FC4 支持状态不明、联锁状态未知、操作超时或反馈不一致。超时/不确定后框架会留下持久事故锁；严格按 `docs/SAFETY_INCIDENT_RUNBOOK.md` 独立读回、保存证据并显式确认，严禁删锁或自动重发。

### H05（P1，T12 剩余项）：真实 EMS Unsolicited 与异常时序

唯一目标：在现有显式 Enable/Disable/Wait 和有界持久收集器上，完成真实 EMS 基线及 Confirm/重发异常时序证据；不要重写已有模块，也不要同时实现周期扫描。

先读：固定 OpenDNP3 Master 配置、`ISOEHandler`/`IMasterApplication`、开发指导书 M3、PICS 主动上送策略和标准内部条款。

已有入口：`OpenDnp3UnsolicitedSupport.cpp`、host/Python 的 `enable_unsolicited` / `disable_unsolicited` / `wait_unsolicited`，以及 `examples/pytest_ems/test_unsolicited_scenarios.py`。先在私有计划填写确定性外部触发、期望点/值/时间戳并启用对应场景，用现成模板验证 G60V2/V3/V4、G2V2/G32V7、启停和 `dropped_total==0`。模板不会发控制制造事件，并保证退出时 Disable。只在确需异常时序且评审通过后，增加隔离 Raw FaultPlan/代理；不得让回调阻塞命令线程。

已有本机测试覆盖事件上送、启停、禁用后无新事件、会话/来源标记和队列溢出。剩余测试：空 unsolicited、Confirm 可见证据、序号回绕、重发/重复、Confirm 丢失、solicited 交错和重连；随后在真实 EMS 按 PICS 验证。不能因 OpenDNP3 内部自动 Confirm 就宣称这些异常路径已覆盖。

验收：每条记录能区分 solicited/unsolicited、会话、分片和接收顺序；重复/缺失策略可解释；队列有上限；断开时无悬挂回调。

停止条件：PICS 禁止 unsolicited、厂商仍声称 Class/Event Read 恒为空却未给出偏差批准、标准时序未复核、无法生成确定性事件或 OpenDNP3 回调所有权不清楚。

### H06（P1，T13）：时间同步

唯一目标：只实现 EMS PICS 指定的一种时间同步流程；Delay Measure/Write Time 与 Record Current Time 应分卡实施。

先读：PICS、EMS 时钟精度/时区定义、固定 `IMasterOperations` 和 `IMasterApplication` API、标准第 10 章内部索引。

要求：使用 UTC 和单调计时记录请求/响应；保存同步前后 DUT 时间、往返延迟和预算；时间写入用例标记 `dnp3_state_changing`；超时不盲目重试。

验收：本机确定性应用回调测试、边界/超时/断线测试、真实 EMS 偏差断言和证据齐全。

停止条件：时区/闰秒/时钟来源不明、无修改 DUT 时钟授权、无法获得同步前后读数或延迟预算。

### H07（P1，T14）：Restart、Freeze、Assign Class 分三卡

这三项不得合并提交：

- H07a Cold/Warm Restart：返回延时、断线/重连、启动 IIN 和恢复时间。
- H07b Freeze：Immediate、Freeze-and-Clear、Freeze-at-Time 及 PICS 允许的 No Response；操作前后读回。
- H07c Assign Class/周期扫描：Class 分配确认、点表更新和独立调度器生命周期。

三卡均需 `dnp3_state_changing`、书面授权、回退方案和真实 EMS。先从固定 OpenDNP3 API 证明能否表达；缺少公共 API 时登记 M5 缺口，不自行拼未经复核的原始帧。

通用停止条件：EMS 负责人不能接受重启/计数冻结/Class 修改，或测试可能影响其他系统连接。

### H08a（P1，T15a～T15d，可现在开发）：持续 MeasurementStore 与 capture API

唯一目标：在已有单次 detail/summary 基础上，用捆绑的回环从站实现 `capture.begin/progress/end`、持续有界汇总和完整协议/Python/打包合同；不连接真实 DUT。

先读：`HostController.cpp` 中当前 `stats`、Read/Unsolicited 支持、指导书第 10 节/M4、Schema 和 fake host。当前 `capture.begin` 是已知但明确不支持的命令，不要把它误当成已有功能。

设计必须先确定：capture ID、状态机、source filter、期望集合、唯一键、重复/缺失/错值的“可证明/unknown”语义、样本上限、队列水位、deadline、取消/断开行为、结果大小和并发规则。v1 仍只允许一个 ACTIVE capture/一个会话。没有外部真值时不得输出伪造的 `missing=0`。

本机验收：summary 不构造逐点 JSON；detail/样本均有硬上限；进度单调；begin/end 幂等规则明确；断开/超时/host shutdown 清理；故意溢出返回稳定终态并保留各层计数；native、Python、真实回环和解包 self-test 全通过。

停止条件：Capture v1 合同或各层 overflow/source/scope 定义尚未评审。EMS 的最终点数和事件率不是 H08a 的阻塞项，可先用确定性本机 Profile。

### H08b（P1，进入内网后）：目标 Profile 映射

唯一目标：把 H08a 的通用 capture 合同映射到获批 EMS 点表、事件源、唯一键、真值/匹配规则和报告样本策略；原则上只改私有 Profile/计划，不新增协议能力。

验收：Profile 哈希进入证据；每个完整性结论都有外部真值或明确为 `unknown`；目标负载仍在 H08a 已测工具容量和内存预算内。

停止条件：点表、事件发生器、目标负载或真值规则不明确。

### H09a（P1，T16a～T16d，可现在开发）：本机性能与稳定性工具链

唯一目标：在 H08a 完成后，用可扩展回环从站建立确定性发生器、Windows 资源采样、性能 Profile/报告、短时 benchmark/soak 和可中断 24 小时 runner；不新增协议能力，不形成 EMS 性能结论。

补齐指标：task submit、首分片、首对象、末对象、task completion、CPU、工作集、private bytes、句柄、线程和各层队列水位。网络/wire 字节、DUT 资源或 wire first byte 没有批准数据源时必须为 `null`，不得估算。报告使用明确统计总体和 nearest-rank p50/p95/p99/max，并绑定构建、Profile、seed、真值清单和环境哈希。

本机验收：大总召、突发/持续事件、只读重连和获批的回环控制负载；runner 支持 watchdog、原子检查点、磁盘/日志上限及中断后报告；故意泄漏/丢弃/真值缺失会稳定失败。缩短 soak 用于 CI，24 小时本机运行只验证工具生命周期，不代表目标 EMS。

停止条件：H08a 未完成、发生器不能提供确定性真值、资源计数 source/scope 不清或 runner 会无界写盘。目标 EMS 阈值和独占环境不是 H09a 工具开发的阻塞项。

### H09b（P1，T16e，仅目标环境）：正式大点表、性能与 24 小时证据

唯一目标：在独立参考端和目标内网独占环境执行已评审的 H09a runner，只产出环境证据、阈值结论和已批准偏差，不顺带修改协议能力。

输入：目标点数、事件率、延迟/吞吐、CPU/内存/句柄/线程阈值、测试机规格、电源计划、网卡、外部真值、允许重连策略、24 小时有效时长以及证据保留位置。

验收：所有可证明场景 `missing==0`，所有层 overflow/drop 为 0，资源趋势和延迟/吞吐满足 Profile；不可观测指标为 `null` 并解释；独立端身份、PCAP/报告、构建、配置和环境均有 SHA-256。批量控制只有单独书面授权和安全门时才执行，否则从 Profile 删除。

停止条件：没有批准阈值、采集会干扰 DUT、测试环境不能独占、参考端低于目标负载、真值不可证明或 H08b/H09a 未通过。

### H10（按 PICS，逐个任务）：Serial、UDP、TLS 或 TCP Server

当前只有 TCP Client。每种新承载必须单独任务、单独配置模型、Schema、fixture、生命周期和独立互操作测试。

TLS 还需批准的证书/密钥管理、协议版本/密码套件策略和无秘密日志；证书私钥不得进入仓库。只有 EMS PICS/部署拓扑确实需要时才实施。

停止条件：PICS 不需要该承载、角色不清楚、串口参数/证书策略缺失或固定 OpenDNP3 构建未启用所需特性。

### H11（M5，每个能力族单独任务）：经典对象与公共 API 缺口

候选项：Group 0；Group 31/33；Group 34；Group 50V1/V2 和 Group 80；`DIRECT_OPERATE_NR`；Command Status 19～125 的原始线上值保真；广播/self-address；公共 Header 无法表达的限定词。Group 13/43 的 solicited/unsolicited 回调归一化已经存在，当前任务是补变体级 golden vector 和独立互操作；只有测试证明 codec/API 真有缺口时才进入后端扩展，不能把它当成空白模块重写。

每项先完成“codec、公共 Master API、回调、事务、独立互操作”五栏源码证据。优先使用固定公共 API；确有缺口时再建立小型 `ExtendedOpenDnp3Backend`/受控 fork，记录 fork commit。

验收：黄金字节 encode/decode、错误对象、限定词、多分片、独立双向互操作、Debug/Release/ASan。内部 fork 两端互测不能算独立互操作。

### H12（M6，每个事务模块单独任务）：高级业务事务

候选模块：File/Group 70、Data Set/83/85–88、Virtual Terminal/112–113、Device Storage/Profile/Application ID/Activation Status、Initialize/Start/Stop Application/Activate Configuration、Group 101/102。

每个模块先提交状态机设计，明确状态、输入、超时、重试、幂等、资源/路径上限和断线清理；评审通过后再编码。不能只做单帧解析就宣称事务完成。

停止条件：PICS 不支持、没有真实业务场景/大小上限或测试端不能模拟错误 handle/block/断线。

### H13（M7，安全批准前 BLOCKED）：Secure Authentication v5

前置条件全部满足前保持 `BLOCKED`：安全负责人批准、正式条款/勘误、批准的密码库、密钥存储与轮换策略、正式测试向量、独立 SAv5 端和威胁模型。

禁止内网 agent 自行实现密码原语、把安全令牌当 SAv5、把密钥放进 JSON/环境日志、PCAP 注释或崩溃转储。满足前置条件后仍需把编解码、状态机、密钥生命周期、审计和负向测试拆成多卡。

### H14（M8/M9）：隔离 fuzz、最终互操作与发布

先在完全离线解析器入口做链路/传输/应用/对象/限定词/长度/CRC/序号单变量 FaultPlan 和 fuzz；绝不连接生产或未经授权设备。每个失败保留最小复现，先排除框架自身缺陷。

发布阶段按 PICS 生成适用/不适用清单，与独立实现及批准的一致性工具执行适用用例，汇总证据、偏差和 waiver，生成 SBOM、许可证、签名包和可复现构建说明。

最终门禁：宣称范围内不能有 `NOT_ANALYZED`、`PLANNED` 或 `IMPLEMENTED_UNVERIFIED`；每个 `VERIFIED_*` 都通过 `relative/path#sha256=<64 hex>` 追溯到实际不可变证据，并具备所需独立性元数据。做不到就缩小宣称范围，不得改状态掩盖缺口。

## 7. 推荐执行顺序

```text
标准分支：H00 requirement catalog ───────────────────────────────┐
                                                                  │
DUT 分支：H01 PICS/点表 -> H02 只读 -> H03 独立证据              │
                              ├-> H04 授权控制（按需）             │
                              └-> H05/H06/H07（只做 PICS 所需）    │
                                                                  ├-> H14 发布门
工具分支：H08a 本机 capture -> H09a 本机 runner                   │
              └-> H08b 目标 Profile -> H09b 正式性能/24h 证据 ───┘

扩展分支：H10/H11/H12（按 PICS/发布 Profile 单项启动）
安全分支：H13（仅全部安全前置条件满足后）
```

进入内网后，对真实 EMS 最有价值的第一张卡仍是 H01。与此同时，外网/本机开发可独立推进 H00、H08a，随后推进 H09a；它们不应因尚未取得 EMS 点表阈值而停滞。H08b/H09b 才依赖目标点表、阈值和独占环境。

## 8. 每张卡的交付回复模板

要求内网 agent 每次只用以下格式交付：

```text
当前任务卡：
唯一目标是否完成：是/否
修改文件：
能力 ID 变化：
测试命令与结果：
证据位置与 SHA-256：
仍然存在的风险/阻塞：
是否触碰真实 DUT/是否改变状态：
建议下一张任务卡（只写一个）：
```

如果任务失败，保留稳定错误、诊断和最小复现；不要用跳过断言、放宽 Schema、扩大超时或返回假数据来制造绿色结果。
