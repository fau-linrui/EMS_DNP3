# DNP3 Windows 主站自动化测试框架开发指导书

> 目标标准：IEEE 1815-2012
> 目标平台：Windows x64
> 核心技术：C++17、OpenDNP3 3.1.2、CMake、Python、pytest、JSON Lines（NDJSON）
> 主要用途：以主站身份自动测试作为 DNP3 从站的 EMS
> 适用读者：项目负责人、测试开发工程师、内网代码 Agent
> 文档状态：实施基线 v1.2
> 文档性质：目标规范与实施路线；当前实现状态以 `config/capability_matrix.csv`、`docs/protocol.md` 和发布包 `bin/build-info.json` 为准

---

## 0. 必须先理解的结论

### 0.1 本项目能做什么

本项目要建立一个 Windows 下运行的 DNP3 主站自动化测试框架：

```text
pytest 测试用例
    -> Python Dnp3MasterClient
    -> stdin/stdout JSON Lines
    -> dnp3-master-host.exe
    -> OpenDNP3 或后续扩展后端
    -> 被测 EMS（DNP3 Outstation）
```

第一阶段使用 OpenDNP3 完成常用且成熟的主站功能；后续通过内部维护分支或可替换后端补齐 OpenDNP3 缺失的 IEEE 1815-2012 功能。

### 0.2 “囊括全部 IEEE 1815-2012 功能”的准确含义

这里的“全部功能”定义为：

1. 对 IEEE 1815-2012 中每个已分配的协议层能力、功能码、对象组/变体、限定词、质量位、IIN 位和安全能力建立唯一能力条目。
2. 每个能力条目必须有标准依据、实现状态、适用方向、测试用例和测试证据。
3. 框架最终能够生成合法请求、解析合法响应，并能验证被测 EMS 的 Device Profile、独立 PICS 和批准操作约定所声明能力的行为。
4. 对 EMS 未声明支持的可选能力，框架验证其是否按标准返回不支持或相应 IIN，而不是强迫 EMS 实现全部可选功能。
5. 保留异常帧和错误时序测试能力，但保留字节值、保留功能码不算“协议功能”。

以下情况不得声称“完整支持”或“符合 IEEE 1815-2012”：

- 只有枚举或空接口，没有线上报文测试。
- 只和同一个 OpenDNP3 从站互通。
- 只有自编单元测试，没有独立实现互操作证据。
- 没有合法获取并逐条核对 IEEE 1815-2012 正式文本。
- OpenDNP3 不支持的功能仍被标记为已完成。
- DUT 的 Device Profile、PICS 和批准操作约定未经过冲突检查并纳入测试选择逻辑。

性能、资源占用、大点表和 24 小时稳定性是本项目为了证明测试工具可用、可信而增加的工程验收要求，不是 IEEE 1815-2012 一致性条款。性能测试通过不能提升协议能力的一致性状态；协议互操作通过也不能代替性能验收。

### 0.3 必须接受的技术事实

未修改的 OpenDNP3 不能覆盖 IEEE 1815-2012 全部能力。根据其公开功能说明，OpenDNP3 对较低子集支持较好，但对下列能力存在部分或完全缺口：

- 设备属性（Group 0）部分/缺失。
- 模拟量死区对象（Group 34）缺失。
- Self-address、部分广播行为缺失。
- 命令事件（Group 13、43）的当前回调映射已接入，但变体级黄金向量和独立互操作证据仍缺失。
- 文件传输（Group 70）没有完整业务实现。
- Data Set（Groups 83、85～88）没有实现。
- Virtual Terminal（Groups 112、113）没有实现。
- Secure Authentication v5（Groups 120～122）没有实现。

因此，本项目的完成路径只能是以下二者之一：

1. 在公司内部维护的 OpenDNP3 分支中补齐缺口，并承担长期维护责任；或
2. 保持统一 Python API，未来替换为确实覆盖这些功能的其他协议栈。

不得让 Agent 假装 OpenDNP3 已经实现上述能力。

### 0.4 本指导书的规范边界

- 本文中的“必须”“不得”“禁止”是项目约束；“建议”“未来”“后续”是设计方向，不能据此宣称功能已经实现。
- 文档版本与软件版本相互独立。`实施基线 v1.2` 不表示 host 或 Python 包的版本为 1.2。
- 本文第 12 节只是能力目录的人工交叉检查入口，不替代正式标准，也不是当前实现清单。
- 当前命令、字段、错误码和安全行为以 `docs/protocol.md` 为唯一公开合同；本文修改这些内容时必须同步协议文档、Schema、C++、Python 和测试。
- 当前能力与证据以能力矩阵的发布快照为准。计划项、接口草图或同栈回环测试不得提升实现或互操作状态。

---

## 1. 给内网 Agent 的总执行提示词

将下面整段内容作为内网 Agent 的项目级指令。每次只给 Agent 一个里程碑中的一个任务，不允许一次性要求它完成整份指导书。

```text
你正在实现一个 Windows x64 下的 DNP3 主站自动化测试框架。

必须遵守：
1. 目标标准固定为 IEEE 1815-2012。不得仅凭记忆实现协议字段；每个协议行为必须引用项目内 capability_matrix.csv 中的标准条目。
2. 当前核心协议栈固定为 OpenDNP3 3.1.2。调用 API 前必须读取本仓库固定版本的头文件和官方 example，禁止猜测 API 名称或混用 2.x 示例。
3. OpenDNP3 不支持的能力必须明确返回 UNSUPPORTED_BY_BACKEND，禁止伪造成功、禁止用 TODO 空实现冒充完成。
4. 每次只完成当前任务。不要顺手重构无关代码，不要提前实现下一阶段。
5. 先阅读相关代码和测试，再给出小型计划；先补测试，再实现；最后必须实际执行构建和测试命令。
6. stdout 只能输出一行一个 UTF-8 JSON 协议消息；所有日志写入 stderr。禁止在 stdout 使用 printf/cout 输出调试信息。
7. OpenDNP3 回调中只允许复制必要数据并入队；禁止阻塞、等待 RPC、执行磁盘 I/O 或直接并发写 stdout。
8. v1 只允许一个主站会话、一个在途 RPC 请求。除非任务明确要求，不要引入 gRPC、HTTP、数据库、GUI、Windows Service 或分布式组件。
9. 大数据量模式不得逐点向 Python 输出 JSON；应在 C++ 内聚合，只返回汇总和异常样本。
10. 不得记录密钥、证书私钥、会话密钥或明文认证材料。
11. 不得声称“通过 IEEE 1815-2012 认证”。只能报告具体能力项及其测试证据。
12. 如果缺少正式标准、DUT PICS、协议栈源码、测试设备或关键输入，停止该能力的实现，输出 BLOCKED 以及所缺材料，不要猜。

每次完成任务后，必须按以下格式回答：
- 当前任务：
- 阅读过的关键文件：
- 修改文件：
- 实现内容：
- 执行的命令：
- 测试结果：
- capability_matrix.csv 更新项：
- 未解决问题/风险：
- 下一建议任务（只写一个）：
```

---

## 2. 项目目标、范围与非目标

### 2.1 项目目标

框架必须支持以下四类工作：

1. **功能自动化**：总召、Class 扫描、遥信/遥测/计数器读取、主动上送、遥控、遥调、时间同步、重启、冻结、类别分配等。
2. **协议一致性验证**：依据 EMS 的 Device Profile、独立 PICS 和批准操作约定验证功能码、对象、变体、限定词、质量位、IIN、确认与时序。
3. **健壮性测试**：错误 CRC、错误序号、截断、重复、乱序、非法组合、超时、断连和重连。
4. **性能测试**：大量测点总召、大量事件上送、批量命令、吞吐量、完整性、延迟分布、队列水位和工具自身丢弃检测。该项属于项目工程验收，不属于 IEEE 1815-2012 一致性判定。

### 2.2 第一版非目标

第一版不得实现：

- GUI。
- 常驻 Windows Service。
- 多租户、多客户端共享服务。
- 跨机器 RPC。
- 同进程 Python 原生扩展（pybind11/.pyd）。
- 在 OpenDNP3 正在使用的同一 TCP 连接上注入手工原始帧。
- 未经安全评审的自研密码算法。

### 2.3 目标版本固定

- 协议标准：IEEE 1815-2012。
- OpenDNP3：3.1.2，必须记录完整源码 commit 和源码包 SHA-256。
- Windows：x64。
- C++：C++17。
- 构建：Visual Studio 2022 + CMake。
- Python：以公司当前受支持版本为准，最低版本在 `python/pyproject.toml` 固定。

如需支持 IEEE 1815 后续修订版，必须创建新的能力矩阵版本，不能静默改变 2012 行为。

---

## 3. 规范来源及优先级

实现时按以下优先级裁决冲突：

1. 公司合法持有的 IEEE 1815-2012 正式文本及适用勘误。
2. 被测 EMS 的标准化 DNP3 Device Profile；如果另有 PICS，应作为独立输入保存并绑定设备型号、固件和文档版本。
3. 项目批准的厂商操作约定、互操作约束和偏差单。它们可收窄适用测试或选择允许的标准选项，但不能改写标准的强制行为。
4. DNP Users Group 与目标版本匹配的测试程序、技术公告和勘误。
5. OpenDNP3 3.1.2 固定源码、API 文档和测试。
6. Wireshark 解码、第三方设备手册和公开示例，仅作为辅助证据。

本文使用以下术语，三者不得混为同一个文件：

| 术语 | 项目含义 | 使用规则 |
|---|---|---|
| DNP3 Device Profile | 按标准结构描述设备能力、对象、功能和选项的设备档案 | 优先用于适用性选择；记录原文件 SHA-256、设备型号和固件 |
| PICS | 项目或一致性流程提供的协议实现声明 | 单独保存；不得因为名称含 PICS 就假设其内容完整或符合标准模板 |
| 厂商操作约定 | 例如本项目收到的“五遥对象、功能码、限定词和主动上送约定” | 视为部分项目 Profile；逐项映射能力 ID，未出现的能力仍是 `UNKNOWN`，不能推断为不支持 |

硬性规则：

- 没有正式标准文本时，可以开发工程骨架和 OpenDNP3 已知功能，但不能关闭“完整 IEEE 支持”相关任务。
- Agent 不得从博客、搜索摘要或其他语言协议栈直接推断标准要求。
- 标准文本受版权保护，不要将整份标准复制进仓库；仓库只记录条款号、表号、能力摘要和内部访问位置。

仓库必须包含：

```text
docs/standards/standard_access.md
docs/standards/ems_device_profile.md
docs/standards/opendnp3_gap_analysis.md
config/capability_matrix.csv
```

`standard_access.md` 至少记录：标准名称、版本、内部受控访问位置、授权主体/依据、负责人、获取日期、勘误版本和文件 SHA-256。正式 PDF、授权水印和许可证文件不得提交公共仓库或进入发布包；显示第三方受限授权的副本不能自动视为公司已获授权的标准来源，应由负责人确认合法访问方式后再关闭标准复核任务。

---

## 4. 总体架构

### 4.1 进程边界

```text
┌──────────────────────────────────────────────┐
│ Python                                      │
│ pytest / fixtures / assertions / reports    │
│ Dnp3MasterClient / point model / PICS filter│
└───────────────────┬──────────────────────────┘
                    │ stdin/stdout NDJSON
┌───────────────────▼──────────────────────────┐
│ dnp3-master-host.exe                        │
│ command loop / backend / event store / perf │
└───────────────────┬──────────────────────────┘
                    │ DNP3 TCP/TLS/UDP/Serial
┌───────────────────▼──────────────────────────┐
│ EMS Outstation                              │
└──────────────────────────────────────────────┘
```

### 4.2 后端边界

C++ 通过已实现的可替换后端接口隔离 Python 用例与 OpenDNP3。当前接口以 `native/include/dnp3host/Backend.h` 为准，核心形态如下（省略具体配置结构）：

```cpp
class IMasterBackend {
public:
    virtual ~IMasterBackend() = default;
    virtual std::string name() const = 0;
    virtual std::string version() const = 0;
    virtual std::vector<std::string> supported_commands() const = 0;
    virtual Json capabilities() const = 0;
    virtual BackendStatus status() const = 0;
    virtual BackendOperationResult connect(const ConnectionConfig&) = 0;
    virtual BackendOperationResult disconnect() = 0;
    virtual BackendOperationResult integrity_poll(const ReadOptions&) = 0;
    virtual BackendOperationResult class_poll(const ClassPollConfig&) = 0;
    virtual BackendOperationResult read(const ReadConfig&) = 0;
    virtual BackendOperationResult enable_unsolicited(const UnsolicitedControlConfig&) = 0;
    virtual BackendOperationResult disable_unsolicited(const UnsolicitedControlConfig&) = 0;
    virtual BackendOperationResult wait_unsolicited(const WaitUnsolicitedConfig&) = 0;
    virtual BackendOperationResult select_and_operate(const CommandConfig&) = 0;
    virtual BackendOperationResult direct_operate(const CommandConfig&) = 0;
    virtual BackendOperationResult wait_event(const WaitEventConfig&) = 0;
    virtual void shutdown() noexcept = 0;
};
```

第一版实现 `OpenDnp3Backend`。未来可实现：

- `ExtendedOpenDnp3Backend`：公司内部扩展分支。
- `RawDnp3Backend`：独立连接的异常报文/原始协议测试。
- `OtherStackBackend`：替换协议栈。

禁止两个后端同时拥有同一 socket。若切换到 Raw 模式，必须先完整关闭 OpenDNP3 会话。

### 4.3 设计原则

- Python 负责测试语义，C++ 负责协议和高效数据收集。
- 正常功能路径使用 OpenDNP3 公共 API，不修改内部报文。
- 未支持能力必须显式失败，不能静默降级。
- 控制面使用 JSON；大量数据面在 C++ 内存中聚合。
- 所有外部可见结构都有 `schema_version`。
- 所有资源具有确定的创建、启用、禁用、销毁顺序。

---

## 5. 当前仓库结构与可移植边界

```text
dnp3-master-test-framework/
├── CMakeLists.txt
├── CMakePresets.json
├── README.md
├── CHANGELOG.md
├── AGENTS.md
├── LICENSES/
├── cmake/
├── third_party/
│   ├── opendnp3/                 # 固定 3.1.2 源码
│   └── *.lock.json               # 来源、commit、SHA-256 和许可证锁
├── native/
│   ├── include/dnp3host/
│   │   ├── Backend.h
│   │   ├── CaptureConfig.h
│   │   ├── MeasurementCapture.h
│   │   ├── OpenDnp3Backend.h
│   │   ├── HostController.h
│   │   ├── JsonLineProtocol.h
│   │   ├── OpenDnp3ReadSupport.h
│   │   ├── OpenDnp3UnsolicitedSupport.h
│   │   ├── OpenDnp3CommandSupport.h
│   │   ├── Ieee1815_2012.h
│   │   ├── Models.h
│   │   └── *Config.h
│   ├── src/
│   │   └── *.cpp
│   └── tests/
├── python/
│   ├── pyproject.toml
│   ├── src/dnp3_master/
│   │   ├── client.py / models.py / errors.py
│   │   ├── pytest_plugin.py
│   │   ├── ems_profile.py / point_table.py / ems_test_plan.py
│   │   ├── preflight.py / evidence.py / safety_incidents.py
│   │   ├── local_outstation.py / package_verify.py / self_test.py
│   │   └── performance.py / local_benchmark.py / process_metrics.py / soak.py / reporting.py
│   └── tests/
├── examples/pytest_ems/           # 可复制到现有 pytest 框架的场景模板
├── examples/pytest_performance/   # 可复制的只读性能与 soak 场景模板
├── schemas/
│   ├── request.schema.json
│   ├── response.schema.json
│   ├── ems-profile.schema.json
│   ├── point-table.schema.json
│   ├── ems-test-plan.schema.json
│   ├── capture/performance/soak/local-event schemas
│   └── evidence/build/package/preflight/safety schemas
├── config/
│   ├── capability_matrix.csv
│   ├── ems_profile.example.json
│   ├── ems_test_plan.example.json
│   ├── performance_profile.example.json
│   ├── local_event_profile.example.json
│   └── points.example.csv
├── docs/
│   ├── architecture.md
│   ├── protocol.md
│   ├── PERFORMANCE_AND_SOAK_GUIDE.md
│   ├── BEGINNER_MIGRATION_BUILD_USE_GUIDE.md
│   ├── INTRANET_HANDOFF_REMAINING_TASKS.md
│   └── standards/
└── scripts/
    ├── doctor.ps1 / build.ps1 / test.ps1
    ├── test-lifecycle.ps1 / run-local-self-test.ps1
    ├── package.ps1
    └── validate_*.py
```

`out/build/<preset>` 是本机 CMake 中间产物，不能作为移植目录复制。移植应优先使用 `scripts/package.ps1` 生成的 `out/package/ems-dnp3-pytest-<version>/` 或其 ZIP；详细步骤以 `docs/BEGINNER_MIGRATION_BUILD_USE_GUIDE.md` 为准。

---

## 6. Windows 构建与依赖管理

### 6.1 依赖规则

- 不在构建时访问公网。
- OpenDNP3 和 nlohmann/json 从公司批准的内部镜像或审核后的源码包获取。
- 固定版本、完整 commit、SHA-256 和许可证。
- 禁止使用浮动 `main/master/release` 分支。
- 所有依赖许可证放入 `LICENSES/`。

### 6.2 构建预设

至少提供：

- `windows-msvc-debug`
- `windows-msvc-release`
- `windows-msvc-asan`（环境支持时）

标准构建命令：

```powershell
cmake --preset windows-msvc-release
cmake --build --preset windows-msvc-release --parallel
ctest --preset windows-msvc-release --output-on-failure
```

### 6.3 构建产物

当前 `scripts/package.ps1` 生成的可移植包至少包括：

```text
bin/dnp3-master-host.exe
bin/build-info.json
CHANGELOG.md
tools/dnp3-local-test-outstation.exe
python/pyproject.toml
python/src/dnp3_master/
examples/pytest_ems/
examples/pytest_performance/
config/capability_matrix.csv
config/*.example.json
config/points.example.csv
schemas/*.json
docs/
dependency-locks/*.lock.json
licenses/
NOTICE.txt
THIRD_PARTY_LICENSES.txt
package-manifest.json
self-test.ps1
```

`bin/build-info.json` 还必须绑定主程序版本、Git commit/工作区状态、OpenDNP3 commit、编译器、构建配置、目标架构、schema 版本、依赖锁哈希和能力矩阵哈希。ZIP 旁必须保留 `.sha256`；解包后先运行 `self-test.ps1`，不能只检查 EXE 是否存在。0.6.0 的 self-test 除读回/控制反馈循环外，还必须对 BI/AI/BOS/AOS 共 8 点完成精确静态 capture，并报告 expected/received unique 均为 8、`valid=true`。

---

## 7. 简化版 NDJSON 控制协议

### 7.1 传输约束

- Python 使用 `subprocess.Popen` 或 `asyncio.create_subprocess_exec` 启动 EXE。
- stdin/stdout 编码固定 UTF-8，无 BOM。
- 每行正好一个 JSON 对象，以 `\n` 结束；输入兼容 `\r\n`。
- v1 同一时间只允许一个请求等待响应。
- stdout 禁止出现任何非协议文本。
- stderr 用于普通日志；性能模式限制日志级别。
- 配置最大请求行长度，超过限制返回 `REQUEST_TOO_LARGE`，不得无限分配内存。

### 7.2 请求与响应信封

请求：

```json
{"schema_version":1,"id":"req-1","cmd":"hello","params":{}}
```

成功响应：

```json
{"schema_version":1,"id":"req-1","ok":true,"result":{}}
```

失败响应：

```json
{"schema_version":1,"id":"req-1","ok":false,"error":{"code":"NOT_CONNECTED","message":"master is not connected","details":{}}}
```

### 7.3 第一阶段命令

| 命令 | 作用 |
|---|---|
| `hello` | 返回版本、后端和能力摘要 |
| `get_status` | 返回 host、连接、会话、在途任务和安全锁状态 |
| `connect` | 创建并启用一个主站会话 |
| `disconnect` | 禁用并销毁会话 |
| `integrity_poll` | Class 0/1/2/3 完整扫描 |
| `class_poll` | Class 1/2/3 事件扫描 |
| `read` | 指定 Group/Variation/Range 扫描 |
| `direct_operate` | Direct Operate；No Response 模式仅在后端明确声明相应能力时允许 |
| `select_and_operate` | Select Before Operate |
| `wait_event` | 从 C++ 有界队列取通道状态事件；不返回测点变化 |
| `enable_unsolicited` | 显式启用选定的 Class 1/2/3 主动上送 |
| `disable_unsolicited` | 显式禁用选定的 Class 1/2/3 主动上送 |
| `wait_unsolicited` | 等待并消费持久、有界的主动上送测量队列 |
| `capture.begin` | 启动一个跨回调、有界的持续采集 |
| `capture.progress` | 读取当前采集的非消费式进度快照 |
| `capture.end` | 有界排空并幂等结束采集，返回完整终态 |
| `stats` | 获取 host、通道和本地队列统计，并明确每个指标的范围和数据源 |
| `shutdown` | 幂等关闭进程 |

### 7.4 后续命令族

| 命令族 | 功能 |
|---|---|
| `freeze.*` | 立即冻结、冻结清零、定时冻结及 No Ack 形式 |
| `time.*` | LAN/Non-LAN 时间同步、延迟测量、读写时间 |
| `unsolicited.fault.*` | Confirm 丢失、重发/重复、序号回绕等原始故障注入；正常启停和等待已由三个公开命令实现 |
| `class.*` | 类别分配与读取 |
| `application.*` | 初始化、启动、停止、保存/激活配置 |
| `restart.*` | 冷启动、热启动、延迟解析 |
| `file.*` | Group 70 文件传输 |
| `dataset.*` | Groups 83、85～88 Data Set |
| `virtual_terminal.*` | Groups 112、113 |
| `security.*` | Secure Authentication v5 与安全统计 |
| `raw.*` | 独占连接的原始帧测试 |

### 7.5 通用读取请求模型

```json
{
  "schema_version":1,
  "id":"req-20",
  "cmd":"read",
  "params":{
    "headers":[
      {"group":30,"variation":0,"qualifier":"all_objects"},
      {"group":1,"variation":2,"qualifier":"range16","start":0,"stop":999}
    ],
    "return_mode":"detail",
    "timeout_ms":5000
  }
}
```

限定词字符串必须通过固定枚举映射到标准编码，禁止让用户直接传未经校验的任意数字；Raw 模式除外。

### 7.6 Hello 响应

当前 `hello` 成功响应形态为（`capabilities` 和 `limits` 内容在此缩写）：

```json
{
  "schema_version":1,
  "id":"req-hello",
  "ok":true,
  "result":{
    "host_version":"0.6.1",
    "backend":"opendnp3",
    "backend_version":"3.1.2",
    "git_commit":"...",
    "platform":"windows-x64",
    "capability_matrix_version":"1",
    "capability_matrix_sha256":"...",
    "supported_commands":[
      "class_poll","connect","direct_operate","disable_unsolicited",
      "disconnect","enable_unsolicited","get_status","hello",
      "integrity_poll","read","select_and_operate","shutdown","stats",
      "wait_event","wait_unsolicited"
    ],
    "capabilities":{"...":"..."},
    "limits":{"...":"..."}
  }
}
```

Python 启动后必须先调用 `hello`。schema 主版本不兼容时立即失败，不得继续测试。

---

## 8. C++ 主程序设计

### 8.1 对象生命周期

生命周期固定为：

```text
读取配置
-> 创建 DNP3Manager
-> 创建 Channel
-> 创建 Master
-> Enable
-> 执行任务
-> Disable/Shutdown Master
-> 释放 Channel
-> Shutdown Manager
-> 退出进程
```

Agent 必须以固定版本 `cpp/examples/master` 和本地头文件为 API 依据。不得直接复制旧版 `asiodnp3/openpal/asiopal` Python 示例中的类型名。

### 8.2 回调处理

至少实现：

- Channel state listener。
- SOE handler。
- Master application callback。
- Task completion callback。
- Command result callback。
- OpenDNP3 log handler。

回调线程处理规则：

1. 读取回调参数。
2. 转换成内部 POD/值对象。
3. 加入有界线程安全队列或 MeasurementStore。
4. 立即返回。

回调线程不得直接等待主线程，不得直接调用 stdout writer。

### 8.3 内部测点模型

当前 host 在线上返回 JSON 值对象，Python 用不可变 `MeasurementRecord` 校验。下列 C++ 仅表示跨边界的逻辑字段合同，不是要求另建一套重复结构：

```cpp
struct MeasurementRecord {
    uint64_t receive_seq;
    uint64_t received_monotonic_ns;
    std::string kind;
    uint8_t group;
    uint8_t variation;
    std::string qualifier;
    uint8_t qualifier_raw;
    std::optional<uint32_t> index;
    Value value;
    std::optional<uint8_t> flags_raw;
    bool flags_valid;
    std::optional<uint64_t> dnp3_timestamp_ms;
    std::string timestamp_quality;
    bool is_event;
    uint32_t header_index;
    std::string source;              // solicited / unsolicited
    uint64_t fragment_index;
    std::optional<uint64_t> session_id;
};
```

要求：

- `receive_seq` 在对应收集器/会话内严格递增；主动上送还必须绑定 `session_id`，不得跨会话比较序号。
- `index` 和 `flags_raw` 必须允许为空，因为并非每种回调对象都携带这两个字段。统一模型应能无损表示 IEEE 1815-2012 的 4-octet index；固定后端当前只能表达 16 位请求索引时，应在能力声明和请求校验中拒绝超范围值，禁止截断或回绕。
- 同时保留限定词名称/原始值和 flags 原始值/有效性，不能只返回 `online=true/false`。
- 区分 DNP3 源时间与本机单调接收时间。
- 浮点数禁止输出 JSON `NaN/Infinity`；应返回明确错误或字符串分类字段。
- Octet String/raw payload 使用 hex 或 Base64，必须限制长度。

### 8.4 错误模型

最低错误码：

```text
INVALID_REQUEST
REQUEST_TOO_LARGE
SCHEMA_MISMATCH
INVALID_STATE
NOT_CONNECTED
ALREADY_CONNECTED
CONNECTION_TIMEOUT
SAFETY_INTERLOCK
ALREADY_EXECUTING
RESPONSE_TIMEOUT
TASK_FAILED
COMMAND_FAILED
UNSUPPORTED_BY_BACKEND
UNSUPPORTED_BY_DEVICE_PROFILE
PROTOCOL_ERROR
QUEUE_OVERFLOW
PROCESS_SHUTTING_DOWN
INTERNAL_ERROR
```

所有错误响应必须包含稳定机器码；`message` 只供人阅读，Python 不得依赖 message 做逻辑判断。

上表是 NDJSON host 错误码的唯一命名表。Python 在本地等待 host 超时应抛出客户端异常（例如 `HostTimeoutError`），不能伪造 `RPC_TIMEOUT` host 响应；`CHANNEL_TIMEOUT`、`DNP3_RESPONSE_TIMEOUT`、`TASK_CANCELLED`、`DUT_REPORTED_ERROR` 和 `COMMAND_NOT_IMPLEMENTED` 不是 v1 host 错误码。连接超时统一为 `CONNECTION_TIMEOUT`，线上任务响应超时统一为 `RESPONSE_TIMEOUT`，设备 IIN/命令状态失败通过结构化结果或 `TASK_FAILED`/`COMMAND_FAILED` 表达。

### 8.5 状态机

```text
STARTING -> READY -> CONNECTING -> CONNECTED -> DISCONNECTING -> READY -> SHUTTING_DOWN
                         |              |
                         +----FAILED----+
```

每个命令列出允许状态。非法状态返回 `INVALID_STATE`，禁止隐式创建或重建连接。

Capture 生命周期是与连接状态正交的子状态机：

```text
IDLE -> ACTIVE -> FINALIZED
          +-------> ABORTED
          +-------> TIMED_OUT
```

v1 每个会话最多一个 ACTIVE capture。`disconnect`、连接失败和 `shutdown` 必须把 ACTIVE capture 转为终态并释放有界资源；新会话不得继承旧 capture。持久控制事故锁属于 Python/DUT 级安全状态，不得因为 native host 回到 READY 而自动解除。

### 8.6 OpenDNP3 3.1.2 API 对照

以下名称来自固定 3.1.2 代码形态，用于防止 Agent 混入旧版 `asiodnp3/openpal/asiopal` API。最终仍以仓库固定 commit 的头文件为准。

| 需求 | 3.1.2 入口/类型 | 注意事项 |
|---|---|---|
| 管理器 | `opendnp3::DNP3Manager` | 日志处理器不能写 stdout |
| TCP client | `DNP3Manager::AddTCPClient` | 使用固定源码中的完整签名；保存 `IChannel` 生命周期 |
| 主站 | `IChannel::AddMaster`、`MasterStackConfig` | SOE handler 和 master application 必须比 master 活得久 |
| 启停 | `IMaster::Enable/Disable` | shutdown 后禁止继续提交任务 |
| 一次扫描 | `Scan`、`ScanAllObjects`、`ScanClasses`、`ScanRange` | 使用独立 `ISOEHandler`/task 上下文关联结果 |
| 周期扫描 | `AddScan`、`AddAllObjectsScan`、`AddClassScan`、`AddRangeScan` | 保存 `IMasterScan`，通过 `Demand` 触发或按生命周期销毁 |
| 通用功能 | `PerformFunction` | 仅能配合公共 `Header` 能表达的对象头；不能假设能携带任意对象 payload |
| 重启 | `Restart`、`RestartOperationResult` | 回调中保留 `TaskCompletion` 和返回延时 |
| 时间/间隔写入 | `Write(TimeAndInterval, index, TaskConfig)` | 不等于所有 Time and Date 读写变体都已支持 |
| 控制 | `CommandSet`、`SelectAndOperate`、`DirectOperate` | 批量结果逐点保存，不能只看 summary |
| 测量回调 | `ISOEHandler::BeginFragment/Process/EndFragment` | 实现固定头文件列出的全部 `Process` overload |
| 主站回调 | `IMasterApplication` | 时间、IIN、任务和应用行为按固定接口实现 |

固定版公共 `Header` 只直接表达 all objects、8/16 位 range、8/16 位 count；其他索引前缀/free-format 不能靠强制转换伪造。固定版 `ICommandProcessor` 公开的是 SBO 和有响应 Direct Operate；如果没有专用 No Response 控制入口，`DIRECT_OPERATE_NR` 必须标为 `UNSUPPORTED_BY_BACKEND` 并在扩展阶段实现，不能把普通 `DirectOperate` 当作等价实现。

---

## 9. Python 客户端设计

### 9.1 对外 API

Python 用例不得直接拼 JSON，统一调用。下面示例与当前 0.6.1 公开 API 一致：

```python
from pathlib import Path

from dnp3_master import (
    Dnp3MasterClient,
    HostProcessConfig,
    ReadHeader,
    TcpConnectionConfig,
)

process = HostProcessConfig(
    executable=Path("bin/dnp3-master-host.exe"),
    safety_incident_directory=Path("evidence/local/safety-incidents"),
)

with Dnp3MasterClient(process) as master:
    master.connect(
        TcpConnectionConfig(
            host="192.0.2.10",
            port=20000,
            master_address=1,
            outstation_address=1024,
        )
    )
    result = master.read([ReadHeader.range16(30, 0, 0, 9)], timeout=5.0)
    assert result.task_status == "SUCCESS"
    assert not {
        "IIN2.0.NO_FUNC_CODE_SUPPORT",
        "IIN2.1.OBJECT_UNKNOWN",
        "IIN2.2.PARAMETER_ERROR",
    }.intersection(result.iin["bits"])
    assert result.iin["observation_window_dropped"] == 0
    analogs = result.measurements_of_kind("analog_input")
    assert analogs
    master.disconnect()
```

最低接口：

```python
connect()
disconnect()
get_status()
integrity_poll()
class_poll()
read()
direct_operate()
select_and_operate()
wait_event()
enable_unsolicited()
disable_unsolicited()
wait_unsolicited()
get_stats()
begin_capture()
capture_progress()
end_capture()
```

公共 `request()` 只允许尚无类型化封装的扩展命令。上述已实现命令（尤其是 connect/disconnect/read/control/shutdown）必须走对应方法；禁止用原始 NDJSON 绕过令牌管理、客户端状态机、结果关联和事故锁。Python 启动时必须校验 host 版本；pytest 还必须把本次能力矩阵 SHA-256 与 host 内嵌值比较，任一不一致立即终止进程。

### 9.2 进程管理

- pytest session fixture 启动 EXE。
- 启动后必须在限定时间内完成 `hello`，并校验 Python/host 版本及能力矩阵哈希。
- 每次写请求后 flush stdin。
- 独立线程持续读取 stderr，避免管道填满死锁。
- 正常结束先发送 `shutdown`。
- 超时后终止进程，并保存 stdout/stderr 尾部、退出码和 dump 路径。
- Windows 使用 Job Object，保证父进程异常退出时清理子进程。
- `pytest-xdist` 下每个 worker 使用独立 EXE；如果 EMS 只允许一个主站，相关测试必须串行。

### 9.3 pytest Fixture 层级

建议：

```text
dnp3_pics               session scope
dnp3_point_table        session scope
dnp3_ems_test_plan      session scope
dnp3_host_config        session scope
host_process            session scope
master_client           session scope（host_process 的别名边界）
dnp3_connection_config  function scope
connected_master        function scope
capture                 function scope（按用例显式创建并在 teardown 中结束）
```

Fixture teardown 必须幂等，前一用例失败不能污染后一用例。

### 9.4 Device Profile、PICS 和操作约定驱动

每个测试用例必须声明能力 ID，例如：

```python
@pytest.mark.dnp3_dut
@pytest.mark.dnp3_capability("APP.FC.01.READ")
@pytest.mark.dnp3_capability("OBJ.G30.V5")
@pytest.mark.dnp3_capability("QUAL.Q01.REVIEW")
def test_static_analog_range(connected_master):
    ...
```

一个 marker 只接受一个矩阵 ID；手写用例必须重复 marker，完整声明实际功能码、对象和 Qualifier。完整性扫描还需 `APP.CLASS.EVENTS`、`QUAL.Q06.REVIEW` 及 `OBJ.G60.V1`～`OBJ.G60.V4`。捆绑的 EMS 场景模板会从严格点表/计划推导这些依赖，普通 pytest 插件不会分析任意函数体来猜测。

运行前先把 Device Profile、PICS 和厂商操作约定映射到同一能力 ID，再按合并后的 DUT 适用性执行：

- `SUPPORTED`：执行正向测试。
- `NOT_SUPPORTED`：默认 skip 正向测试；只有测试计划明确要求且风险允许时才执行“不支持行为”测试。
- `UNKNOWN`：标记 xfail/blocked，不得自动假设支持。

任何状态改变能力即使标为 `SUPPORTED`，仍必须独立通过安全门；Profile 不是操作授权。Profile、PICS 和操作约定存在冲突时不得自动选择其中一个，应在预检阶段阻断并生成可定位的冲突项。

---

## 10. 性能测试设计

### 10.1 两种返回模式

| 模式 | 行为 | 用途 |
|---|---|---|
| `detail` | 返回完整测点明细 | 功能验证和问题定位 |
| `summary` | C++ 内聚合，只返回统计和异常样本 | 大数据性能测试 |

两种模式均必须有接收对象数、内存和响应大小上限。`summary` 不得先构造完整 `MeasurementRecord` JSON 数组再丢弃；它应在 native 层直接更新紧凑统计。一次 Read 的 `return_mode` 只控制该任务结果，持续 capture 则跨多个回调和任务聚合，二者是正交能力。

### 10.2 Capture v1 语义与命令合同

Capture 必须先声明统计语义，不能把所有接收对象都按同一种“唯一点”算法处理：

| `mode` | 真值来源 | 唯一性和完整性规则 |
|---|---|---|
| `static_set` | `capture.begin` 中经过校验的紧凑点范围 | 唯一键为规范化的 `kind + uint32 index`；同一键再次出现计为 duplicate；可确定 missing |
| `event_sequence` | 外部参考从站/发生器生成的不可变事件清单和 SHA-256 | 每次事件是独立 occurrence；同一测点再次变化不自动算 duplicate；由报告层按批准的匹配规则与外部清单对账 |
| `observation` | 无外部真值 | 只报告观察总数、签名重复、顺序和资源；`missing`/`duplicates` 必须为 `null` 并给出 `UNKNOWN_WITHOUT_GROUND_TRUTH` |

`static_set` 的 Variation、来源、flags 和可选期望值属于过滤或 mismatch 断言，不属于点身份；这样 Variation 0 请求返回具体变体时不会被误判为另一个点。浮点期望值必须声明“位模式相等”或明确的绝对/相对容差。大型非连续点表应由 Python 预检后转换成有界紧凑范围；host 不接受任意文件路径，也不接受超过请求行和范围数量上限的内联清单。

`event_sequence` 的外部清单至少绑定发生器版本、seed/场景 ID、开始和结束序号、事件总数及 SHA-256。DNP3 事件本身没有本项目的发生器 occurrence ID；没有外部真值或批准的匹配规则时，框架不得宣称 `missing == 0` 或 `duplicates == 0`。

命令为：

```text
capture.begin
capture.progress
capture.end
```

v1 行为固定如下：

- 每个会话最多一个 ACTIVE capture；`capture.begin` 在已有 ACTIVE capture 时返回 `ALREADY_EXECUTING`。
- `capture.begin` 由 host 生成有界 ASCII `capture_id`，返回规范化后的 mode、过滤条件、期望模型、开始单调时间和 deadline。
- `capture.progress` 必须携带准确 `capture_id`，只返回非消费快照。累计计数、最大队列水位和状态只能单调前进；`current_queue_depth` 允许上下变化。
- `capture.end` 必须携带准确 `capture_id`，停止接收、在有界期限内排空 collector 并返回终态。对同一 ID 重复调用必须幂等并返回相同终态摘要，直到下一次成功 `capture.begin` 或会话销毁。
- 错误、过期或其他会话的 ID 返回 `INVALID_STATE` 并包含当前状态，不得误结束活动 capture。
- `disconnect`、连接失败或 `shutdown` 把 ACTIVE capture 标记为 `ABORTED`，释放资源，并在断开结果或有界诊断快照中保留终态计数。
- 本地 capture deadline 到期后状态为 `TIMED_OUT`、`valid=false`，原因包含 `CAPTURE_DEADLINE_EXCEEDED`；它不是 DNP3 `RESPONSE_TIMEOUT`。
- 队列溢出后继续保留计数，但最终结果无效；`capture.progress/end` 返回 `QUEUE_OVERFLOW`，并在 `details.operation_result` 中携带有界终态摘要。
- 因 v1 只有一个在途 RPC，阻塞的 Read 请求执行期间不保证能够调用 `capture.progress`。持续 unsolicited 场景可在请求间轮询；若将来需要任务执行中的实时进度，必须单独设计异步 job/通知协议，不能暗中增加第二条 stdout 通道。

`capture.begin` 至少接受以下经过 Schema 限制的概念字段；准确字段名和上限在实现任务中固定后同步到 `schemas/`：

```json
{
  "mode":"static_set",
  "sources":["solicited"],
  "expected":{
    "point_ranges":[
      {"kind":"analog_input","start":0,"stop":99999}
    ]
  },
  "mismatch_sample_limit":100,
  "duration_limit_ms":600000
}
```

`capture.end` 的静态点集结果至少返回：

```json
{
  "capture_id":"cap-...",
  "state":"FINALIZED",
  "valid":true,
  "mode":"static_set",
  "expected_total":100000,
  "received_total":100000,
  "received_unique":100000,
  "duplicates":0,
  "missing":0,
  "unmatched_total":0,
  "duration_ms":1840,
  "throughput_per_sec":54347.8,
  "max_queue_depth":418,
  "queue_overflow":0,
  "invalid_reasons":[],
  "completeness_scope":"NATIVE_STATIC_SET",
  "mismatch_sample":[]
}
```

所有计数必须定义为无符号整数并检查溢出。`missing`、`duplicates` 或资源指标不可证明/不可采集时使用 JSON `null` 加稳定 reason/source 字段，禁止填 0、空数组或估算值冒充测量结果。

### 10.3 性能实现约束

- 不得每个测点输出一条 JSON。
- 回调到存储路径不得进行磁盘 I/O。
- 先使用有界 mutex/condition_variable 队列；只有基准证明不够时才改无锁结构。
- 队列满时必须递增 overflow 并让本次结果无效，禁止静默丢弃。
- 可使用数组、位图、紧凑结构或哈希摘要记录接收情况；配置必须同时限制项目数和估算字节数，不能只限制点数。
- 只返回全部异常点中的有限样本，样本上限可配置。
- 性能运行关闭逐帧和逐点 Debug 日志。
- 当前 `read` 已支持多 Header，`direct_operate`/`select_and_operate` 已支持一个 CommandSet 多点；不得仅为改名重复新增 `read_batch`/`operate_batch`。只有现有命令无法表达所需线上任务时才设计新 API。
- 同时测试“一个 CommandSet 多点”和“连续单点命令”，二者结果分开报告。
- native 回调队列溢出、OpenDNP3/链路层丢弃、DUT 事件缓冲溢出 IIN 和操作系统网络丢包必须分别计数；其中任何一个非零都不能被另一个计数掩盖。
- 性能采集开销本身必须通过 capture 关闭/开启的 A/B 基线量化；无法证明观察开销可接受时，不得用该结果评价 DUT。

### 10.4 指标词典和时间口径

每个指标必须在报告中携带 `source`、`scope`、`unit` 和可用性；不同采集层的同名时间不得混用：

| 指标 | 固定定义 | 典型来源 |
|---|---|---|
| `task_submit_monotonic_ns` | host 向 OpenDNP3 提交任务前的本机单调时间 | host |
| `first_fragment_monotonic_ns` | SOE `BeginFragment` 首次进入时的本机单调时间 | OpenDNP3 callback |
| `first_object_monotonic_ns` | 首个对象进入 `Process` 时的本机单调时间 | OpenDNP3 callback |
| `last_object_monotonic_ns` | 最后一个对象进入 `Process` 时的本机单调时间 | OpenDNP3 callback |
| `task_complete_monotonic_ns` | task completion callback 到达时的本机单调时间 | OpenDNP3 callback |
| `wire_first_byte_monotonic_ns` | TCP/链路首字节到达采集点的时间 | 仅限批准的底层插桩或 PCAP；否则为 `null` |
| `throughput_per_sec` | 指定统计窗口内 `received_total / duration_seconds` | native capture |
| `process_cpu_seconds` | host 进程 user+kernel CPU 增量 | Windows process API |
| `cpu_core_percent` | `CPU 秒 / wall 秒 × 100`；一个满载逻辑核为 100%，可超过 100% | Windows process API |
| `working_set_bytes` / `private_bytes` | 同一采样点的当前值，另报 peak | Windows process API |
| `handle_count` / `thread_count` | host 进程当前值，另报 min/max/末值 | Windows process API |
| `queue_depth` / `max_queue_depth` / `queue_overflow` | 项目 collector 队列当前值、峰值和累计溢出 | native collector |
| `network_rx_bytes` / `network_tx_bytes` | 明确是 DNP3 payload、TCP payload 还是线速字节 | PCAP 或经验证的底层计数；否则为 `null` |
| DUT CPU/内存/队列 | 被测 EMS 进程或主机资源 | 内网批准的外部监控；主站不得估算 |

延迟必须声明统计总体，例如“每次完整性扫描的 submit-to-complete”或“每个事件的发生器时间到 first-object”。p50/p95/p99 使用固定、文档化的 nearest-rank 算法并报告样本数；禁止把每点、每分片和每任务延迟混入同一个分布。DNP3 设备时间只有在时钟同步、精度和采集延迟预算已证明时才能用于单向延迟，否则仅作为设备时间字段展示。

### 10.5 必须证明工具不是瓶颈

正式测试 EMS 前，先用独立参考从站测出测试工具上限，并记录：

- 最大持续接收速率。
- 最大总召点数。
- EXE CPU、内存、句柄和队列水位。
- JSON detail/summary 两种模式差异。
- 连接反复创建/销毁稳定性。
- 24 小时或项目规定时长的稳定性。

基准必须记录测试机 CPU/内存/操作系统、电源计划、网卡、进程版本、配置/点表/性能 Profile 哈希、参考端版本和两端是否同机。项目自带 OpenDNP3 回环从站只用于确定性回归和初步容量探索；同栈、同机结果不能作为独立互操作证据，也不能单独证明 master 上限。正式工具上限优先在另一台机器使用独立参考端测量，并确认参考端能力高于目标负载。

每个场景包含可配置预热、测量、冷却和多轮重复。先执行无 capture/有 capture 的对照，再执行目标负载。若任一项目队列 overflow、DUT 事件缓冲溢出、操作系统/抓包丢包、工具 CPU 达到配置上限或队列/内存持续增长，本次 EMS 性能结论无效。阈值和安全余量只能来自项目 `performance_profile.json`，不得写死在代码或指导书中。

### 10.6 24 小时稳定性运行规则

24 小时不是一个简单的长 `sleep`，必须由可恢复证据的 soak runner 编排：

1. 启动前运行离线预检，记录构建、配置、Profile、点表、参考端/DUT 身份和测试机规格哈希。
2. 按 Profile 执行预热后开始正式计时；资源、连接、任务、capture 和磁盘余量按有界周期采样。还必须持续消费有界 channel-event 队列，捕获两次状态快照之间的短暂断线/重连；队列发生任何 drop 时连接历史不可证明，本轮 fail closed。
3. 检查点使用有界轮转的编号文件保存，并通过同目录临时文件加原子替换写入；每个检查点至少包含 UTC、单调经过时间、最后成功场景、累计指标和前一检查点哈希。
4. 日志、PCAP 和样本文件必须轮转并有总大小上限；磁盘余量低于阈值时安全停止并标记 `INCOMPLETE_RESOURCE_LIMIT`，不能写满磁盘。
5. watchdog 检测 host 退出、无进度、连接抖动和采集线程停止。其阈值必须严格大于单个场景已知的全部有界 RPC 预算；启用 capture 时包括 begin、Read 和 end/drain 三段，不能只按 Read timeout 配置。进程崩溃、机器重启、人工中断或证据链断裂都标记 `INCOMPLETE`，不得自动合并成一次连续 24 小时通过结果。
6. 只读场景可按 Profile 做有界重连并单独累计中断；任何状态改变场景不得自动重试或在事故锁未处置时恢复运行。
7. 结束时原子生成最终报告，包含实际有效时长、p50/p95/p99/max、完整性、所有 overflow/drop、连接/超时、资源 min/max/末值和内存增长斜率。

是否通过由 Profile 明确规定，至少要求有效时长达到目标、所有可证明场景 `missing == 0`、所有 overflow/drop 为 0、无未处置异常退出，且预热后的 private bytes、working set、句柄和线程趋势不超过批准阈值。正式 24 小时结论必须在目标内网测试机和独占环境重新执行；本机缩短 soak 只能验证 runner、清理和报告逻辑。

---

## 11. 能力矩阵：整个项目的唯一完成台账

### 11.1 文件格式

`config/capability_matrix.csv` 是标准能力目录和框架实现状态的唯一事实来源，但不是某台 EMS 的 PICS。每台 DUT 的适用性必须通过独立、未提交的 `ems.local.json` 叠加；不能把一个设备的结论写回公共矩阵。表头固定为：

```csv
capability_id,edition,layer,feature,direction,subset_level,function_code,object_group,variations,qualifiers,std_reference,dut_pics_status,backend_status,framework_status,test_case_ids,evidence,owner,notes
```

字段规则：

| 字段 | 规则 |
|---|---|
| `capability_id` | 永久唯一，例如 `APP.FC.01.READ`、`OBJ.G30.V1`、`IIN.IIN1.4.NEED_TIME` |
| `edition` | 固定 `IEEE1815-2012`，不能省略 |
| `layer` | `CHANNEL`、`LINK`、`TRANSPORT`、`APPLICATION`、`SECURITY`、`ROBUSTNESS` |
| `direction` | `M2O`、`O2M` 或 `BIDIRECTIONAL` |
| `subset_level` | 从正式标准/Device Profile 填写；不确定时填 `REVIEW_REQUIRED` |
| `std_reference` | 正式标准的卷、章、条、表或图编号；不得只写网页链接 |
| `dut_pics_status` | 公共框架目录中固定为 `UNKNOWN`；真实 DUT 的声明保存在独立 PICS overlay |
| `backend_status` | 当前 C++ 后端实际能力 |
| `framework_status` | 从 Python API 到线上证据的端到端状态 |
| `test_case_ids` | 一个或多个稳定的 pytest ID |
| `evidence` | 报告、PCAP、测试向量或独立互操作记录；`VERIFIED_*` 必须使用 `relative/path#sha256=<64 hex>` |

当前 426 行矩阵是能力/对象目录，不等于 IEEE 1815-2012 的逐条规范要求清单。要宣称“全部标准要求已纳入”，还必须建立单独的 requirement catalog，把每条 `shall`、`shall not` 和条件性要求拆成稳定 requirement ID，记录条款、前置条件、适用角色、能力 ID、测试 ID 和证据。没有该目录时，只能陈述“能力目录覆盖”，不能陈述“规范要求全覆盖”。

### 11.2 状态枚举

公共矩阵的 `backend_status/framework_status` 使用以下实现状态：

```text
NOT_ANALYZED
UNSUPPORTED_BY_BACKEND
PLANNED
IMPLEMENTED_UNVERIFIED
VERIFIED_UNIT
VERIFIED_INTEROP
VERIFIED_CONFORMANCE
BLOCKED
```

每台 DUT 的 PICS 只允许 `SUPPORTED`、`NOT_SUPPORTED`、`UNKNOWN`。`NOT_APPLICABLE_BY_PICS` 是预检/执行结果中的派生结论，不得写入公共框架矩阵来掩盖后端或框架缺口。

这些状态不是“只能向右”的永久等级。每次发布必须根据当前代码、后端版本、DUT 固件和可访问证据重新计算；测试回归、证据损坏/过期、依赖或固件变化时必须降级。已发布的矩阵快照保持不可变，当前矩阵通过新提交记录升降原因。目标状态至少满足：

| 目标状态 | 最低证据 |
|---|---|
| `IMPLEMENTED_UNVERIFIED` | 代码、代码审查和成功构建 |
| `VERIFIED_UNIT` | 字段级编码/解码测试和固定黄金字节 |
| `VERIFIED_INTEROP` | 与独立实现或真实 EMS 的 PCAP、断言和环境记录 |
| `VERIFIED_CONFORMANCE` | 适用的正式一致性程序结果及人工审核 |

`UNSUPPORTED_BY_BACKEND` 不是失败，而是必须保留的真实状态。DUT 的“不适用”只能用于某次设备执行选择，不能把框架自身未实现的能力隐藏掉。

`VERIFIED_INTEROP` 的证据清单必须记录对端协议栈及其独立性；仅知道“真实 EMS”但不知道是否与本项目同栈时，应保留真实 DUT 证据，但不得把它当作唯一独立互操作依据。DUT 适用性、backend 实现和 framework 端到端证据仍是三个独立维度，不能用一个总状态互相覆盖。

### 11.3 自动门禁

实现 `scripts/validate_capabilities.py`，在 CI 中检查：

- `capability_id` 唯一且格式合法。
- 枚举值合法。
- 2012 目标项都有 `std_reference`。
- `VERIFIED_*` 都有测试 ID、存在的证据文件以及与文件内容匹配的 SHA-256。
- 公共矩阵所有 `dut_pics_status` 均为 `UNKNOWN`，不接受设备状态污染框架目录。
- 测试引用的能力 ID 都存在；矩阵引用的测试 ID 也真实存在。
- `VERIFIED_CONFORMANCE` 不能只有同栈自测证据。
- 发布时不能存在被本次里程碑要求覆盖的 `NOT_ANALYZED`。
- 汇总报告分别显示“标准清单覆盖率”“框架实现率”“DUT 适用项通过率”，严禁把三者合成一个百分比。

---

## 12. IEEE 1815-2012 全能力清单基线

本节用于初始化能力矩阵，不能替代正式标准。Agent 必须逐项对照公司合法持有的 IEEE 1815-2012 文本和与该版匹配的 DNP3 Device Profile；如果名称、变体或适用条件冲突，以正式文本为准并提交人工评审，不得自行选择。

### 12.1 通道和物理承载

至少建立以下能力族：

- 异步串口参数：波特率、数据位、停止位、校验、流控、打开失败和重连。
- TCP client；如项目需要，还包括 TCP listen/server 连接方式。
- UDP profile，包括端点、源地址校验和无连接语义。
- TLS 仅作为安全通道选项；证书链、主机名、协议版本、吊销策略由公司安全基线决定。
- 连接建立、断开、超时、退避重连、半开连接、网卡切换和远端重启。

注意：TLS 通道不等于 DNP3 Secure Authentication v5，不能用 TLS 测试结果关闭 SAv5 能力项。

### 12.2 数据链路层

矩阵必须覆盖：

- 起始字节、长度、控制字节、目的/源链路地址和小端字段编码。
- 每个 16 字节用户数据块对应的 DNP3 CRC，以及首部 CRC。
- `DIR`、`PRM`、`FCB`、`FCV`、`DFC` 位及其合法组合。
- 主站和从站链路功能码、确认与非确认用户数据、链路状态请求/响应、复位链路状态。
- Confirmed User Data 的 FCB 交替、重试、重复帧处理和超时。
- 广播地址类别、禁止响应的广播语义，以及目标项目是否使用 self-address。
- 错误长度、错误 CRC、未知地址、重复帧、失序确认、忙/DFC 和链路断开恢复。
- 串口多点链路与 IP 点到点场景的适用差异。

### 12.3 传输层

矩阵必须覆盖：

- Transport Header 的 `FIR`、`FIN` 和 6 位序号。
- Application Fragment 的分段与重组。
- 最大链路帧和最大应用分片边界。
- 单段、多段、序号回绕、重复段、缺段、乱序段、超时丢弃和断连清理。
- 同一会话内发送与接收方向各自独立的序号状态。
- 超大或恶意声明长度不得造成无界内存分配。

### 12.4 应用控制、响应和任务状态

矩阵必须覆盖：

- Application Control 的 `FIR`、`FIN`、`CON`、`UNS` 和 4 位序号。
- 多分片响应、应用确认、确认超时、重试和重复响应。
- 请求、Solicited Response、Unsolicited Response 的序号空间和关联规则。
- 主动上送的 enable/disable、启动握手、空主动响应、事件主动响应和确认。
- 请求超时、任务取消、断连时取消、重复执行保护及 `ALREADY_EXECUTING`。
- 每次任务的开始/完成原因、IIN 快照、响应头、对象统计、耗时和失败阶段。

### 12.5 应用功能码总表

能力矩阵至少为下列每个已分配功能码创建一行；一个功能码存在多种对象/时序时继续拆成原子行。

| 十六进制 | 十进制 | 名称 | 主要方向 | 实施备注 |
|---:|---:|---|---|---|
| `0x00` | 0 | CONFIRM | 主站→从站 | 对带 `CON` 的从站应用分片确认；IEEE 1815-2012 Table 4-2 / 4.4.1 |
| `0x01` | 1 | READ | 主站→从站 | 静态、事件、Class、范围和多 Header |
| `0x02` | 2 | WRITE | 主站→从站 | 时间、死区、IIN/配置等对象依适用性拆分 |
| `0x03` | 3 | SELECT | 主站→从站 | 与 OPERATE 配对，严格校验选择结果 |
| `0x04` | 4 | OPERATE | 主站→从站 | SBO 时序、值一致性、超时和状态 |
| `0x05` | 5 | DIRECT_OPERATE | 主站→从站 | 需要响应 |
| `0x06` | 6 | DIRECT_OPERATE_NR | 主站→从站 | No Response；本地仍需记录发送结果 |
| `0x07` | 7 | IMMED_FREEZE | 主站→从站 | 计数器/模拟量按标准适用对象 |
| `0x08` | 8 | IMMED_FREEZE_NR | 主站→从站 | No Response |
| `0x09` | 9 | FREEZE_CLEAR | 主站→从站 | 冻结并清零 |
| `0x0A` | 10 | FREEZE_CLEAR_NR | 主站→从站 | No Response |
| `0x0B` | 11 | FREEZE_AT_TIME | 主站→从站 | 时间和周期字段边界 |
| `0x0C` | 12 | FREEZE_AT_TIME_NR | 主站→从站 | No Response |
| `0x0D` | 13 | COLD_RESTART | 主站→从站 | 校验返回延时及重新上线 |
| `0x0E` | 14 | WARM_RESTART | 主站→从站 | 校验返回延时及状态保持 |
| `0x0F` | 15 | INITIALIZE_DATA | 主站→从站 | 2012 中的废止/保留语义须按正式文本测试 |
| `0x10` | 16 | INITIALIZE_APPL | 主站→从站 | 应用标识对象 |
| `0x11` | 17 | START_APPL | 主站→从站 | 应用标识对象 |
| `0x12` | 18 | STOP_APPL | 主站→从站 | 应用标识对象 |
| `0x13` | 19 | SAVE_CONFIG | 主站→从站 | 2012 中的废止/弃用语义须按正式文本测试 |
| `0x14` | 20 | ENABLE_UNSOLICITED | 主站→从站 | Class 1/2/3 任意组合 |
| `0x15` | 21 | DISABLE_UNSOLICITED | 主站→从站 | 启动及运行期行为 |
| `0x16` | 22 | ASSIGN_CLASS | 主站→从站 | 对象范围到 Class 的分配 |
| `0x17` | 23 | DELAY_MEASURE | 主站→从站 | LAN/非 LAN 时间同步流程 |
| `0x18` | 24 | RECORD_CURRENT_TIME | 主站→从站 | 记录当前时间流程 |
| `0x19` | 25 | OPEN_FILE | 主站→从站 | Group 70 会话和权限 |
| `0x1A` | 26 | CLOSE_FILE | 主站→从站 | 成功、重复和异常关闭 |
| `0x1B` | 27 | DELETE_FILE | 主站→从站 | 权限和状态码 |
| `0x1C` | 28 | GET_FILE_INFO | 主站→从站 | 元数据解析 |
| `0x1D` | 29 | AUTHENTICATE_FILE | 主站→从站 | 文件认证，不等于 SAv5 |
| `0x1E` | 30 | ABORT_FILE | 主站→从站 | 会话回收 |
| `0x1F` | 31 | ACTIVATE_CONFIG | 主站→从站 | 激活状态对象和时序 |
| `0x20` | 32 | AUTHENTICATE_REQ | 主站→从站 | SAv5 认证请求；IEEE 1815-2012 Table 4-2 / 4.4.21 |
| `0x21` | 33 | AUTH_REQ_NO_ACK | 主站→从站 | SAv5 无响应请求；IEEE 1815-2012 Table 4-2 / 4.4.22 |
| `0x81` | 129 | RESPONSE | 从站→主站 | Solicited response、IIN、对象解析 |
| `0x82` | 130 | UNSOLICITED_RESPONSE | 从站→主站 | 主动上送及应用确认 |
| `0x83` | 131 | AUTHENTICATE_RESP | 从站→主站 | SAv5 认证响应；IEEE 1815-2012 Table 4-2 / 4.4.25 |

对 No Response 功能，测试不能以“收到成功响应”为通过条件；应根据线上无响应、从站状态后读回和副作用验证共同判定。

### 12.6 对象组/变体总表

下表是建立逐变体能力条目的目录。`v0` 通常表示请求任意/默认变体，但不是所有组都定义 `v0`；不得机械补齐。`1–255` 表示变体号承载长度时，Agent 必须从标准确认合法长度、`v0` 语义和最大值。

| Group | 对象族 | 2012 目录中的变体 |
|---:|---|---|
| 0 | Device Attributes | 标准预定义属性主要为 `v196–250`、`v252`、`v254`、`v255`；另有用户/厂商属性规则，逐项从正式表导入 |
| 1 | Binary Input 静态 | `v0–2` |
| 2 | Binary Input Event | `v0–3` |
| 3 | Double-bit Binary Input 静态 | `v0–2` |
| 4 | Double-bit Binary Input Event | `v0–3` |
| 10 | Binary Output Status 静态 | `v0–2` |
| 11 | Binary Output Status Event | `v0–2` |
| 12 | Binary Output Command | `v0–3`；逐项核对当前/废止语义 |
| 13 | Binary Output Command Event | `v0–2` |
| 20 | Counter 静态 | `v0–8`；`v3,v4,v7,v8` 在 2012 中仍有分配但 obsolete，不得从目录删除 |
| 21 | Frozen Counter 静态 | `v0–12`；`v3,v4,v7,v8,v11,v12` 在 2012 中仍有分配但 obsolete |
| 22 | Counter Event | `v0–8`；`v3,v4,v7,v8` 在 2012 中仍有分配但 obsolete |
| 23 | Frozen Counter Event | `v0–8`；`v3,v4,v7,v8` 在 2012 中仍有分配但 obsolete |
| 30 | Analog Input 静态 | `v0–6` |
| 31 | Frozen Analog Input 静态 | `v0–8` |
| 32 | Analog Input Event | `v0–8` |
| 33 | Frozen Analog Input Event | `v0–8` |
| 34 | Analog Input Reporting Deadband | `v0–3` |
| 40 | Analog Output Status 静态 | `v0–4` |
| 41 | Analog Output Command | `v0–4`；逐项核对 `v0` 的请求合法性 |
| 42 | Analog Output Status Event | `v0–8` |
| 43 | Analog Output Command Event | `v0–8` |
| 50 | Time and Date | `v1–4` |
| 51 | Common Time of Occurrence | `v1–2` |
| 52 | Time Delay | `v1–2` |
| 60 | Class Data | `v1–4` |
| 70 | File Control | `v0–8`；`G70V0` 是 ASSIGN_CLASS 请求中选择 file event `v4–7` 的请求选择器，不是具体响应格式 |
| 80 | Internal Indications | `v1` |
| 81 | Device Storage | `v1` |
| 82 | Device Profile | `v1` |
| 83 | Data Set Private Registration | `v1–2` |
| 85 | Data Set Prototype | `v0–1`；`v1` 为具体 prototype，`v0` 的请求适用性按正式条款核对 |
| 86 | Data Set Descriptor | `v0–3` |
| 87 | Data Set Present Value | `v0–1` |
| 88 | Data Set Snapshot Event | `v0–1` |
| 90 | Application Identifier | `v1` |
| 91 | Activation Status | `v1` |
| 100 | Floating-point（obsolete 通用对象族说明） | 无新的具体数据变体；保留能力条目并禁止新应用误用 |
| 101 | Binary-Coded Decimal 对象族 | `v1–3`；名称和字段须由正式表复核 |
| 102 | Unsigned Integer | `v0–1` |
| 110 | Octet String 静态 | 变体号表示长度；逐长度能力由项目风险抽样，解析器须覆盖全部合法长度 |
| 111 | Octet String Event | `v0` 及长度变体；按正式表确认 |
| 112 | Virtual Terminal Output Block | 变体号表示长度；按正式表确认 |
| 113 | Virtual Terminal Event Data | `v0` 及长度变体；按正式表确认 |
| 120 | Secure Authentication v5 | `v0–15` |
| 121 | Security Statistics | `v0–1` |
| 122 | Security Statistic Event | `v0–2` |

对每个测量对象至少测试：显式变体、`v0` 默认选择、空范围、单点、边界索引、混合 Header、值边界、质量位、绝对/相对时间、Class 读取和事件读取。浮点还要测试 `±0`、有限边界、`NaN` 和 `±Inf` 的项目策略；断言必须比较 IEEE 754 位模式或明确的归一化策略。

### 12.7 限定词（Qualifier）

必须从正式标准建立“限定词代码 × 对象 × 功能码 × 方向”的合法组合矩阵。IEEE 1815-2012 Table 4-6 分配的限定词代码完整集合为：

```text
00 01 02 03 04 05 06 07 08 09
17 18 19 27 28 29 37 38 39
4B 5B 6B
```

目录至少覆盖：

- 8/16 位 start-stop 范围。
- all objects。
- 8/16 位 count。
- 带 8/16/32 位索引前缀的对象序列。
- 16 位 free-format（常用于文件/认证等对象）。
- 标准定义但 OpenDNP3 公共 API 无法生成的限定词。

代码出现在 Table 4-6 只表示它在 2012 中已分配，不表示它对所有对象、功能码和方向都合法；合法组合仍须逐条建模和测试。

固定 OpenDNP3 3.1.2 仅公开 Q00/Q01/Q06/Q07/Q08/Q17/Q28。当前框架不能表达 Q02/Q09/Q39 的 32-bit range/count/index，也不能把它们降级为 16-bit 后声称已执行；需要这些限定符的场景必须由 PICS + 框架双门禁阻止，直到完成经评审的后端扩展或更换协议栈。

每个限定词的测试需覆盖：合法边界、start > stop、count 与实际对象数不符、索引溢出、重复索引、不支持组合及正确 IIN。

### 12.8 IIN 位

必须保留原始 16 位 IIN，并逐位解析：

| 字节.位 | 名称 |
|---|---|
| IIN1.0 | `BROADCAST` |
| IIN1.1 | `CLASS1_EVENTS` |
| IIN1.2 | `CLASS2_EVENTS` |
| IIN1.3 | `CLASS3_EVENTS` |
| IIN1.4 | `NEED_TIME` |
| IIN1.5 | `LOCAL_CONTROL` |
| IIN1.6 | `DEVICE_TROUBLE` |
| IIN1.7 | `DEVICE_RESTART` |
| IIN2.0 | `NO_FUNC_CODE_SUPPORT` |
| IIN2.1 | `OBJECT_UNKNOWN` |
| IIN2.2 | `PARAMETER_ERROR` |
| IIN2.3 | `EVENT_BUFFER_OVERFLOW` |
| IIN2.4 | `ALREADY_EXECUTING` |
| IIN2.5 | `CONFIG_CORRUPT` |
| IIN2.6 | `RESERVED_2`；非零时保留原值并报告 |
| IIN2.7 | `RESERVED_1`；非零时保留原值并报告 |

IIN 测试必须区分瞬时位、保持位以及由后续动作清除的位；不能只判断 `response.ok` 或 OpenDNP3 task `SUCCESS`。IIN2.0/2.1/2.2 表示请求不被支持、对象未知或参数错误，EMS 功能场景必须失败，即使场景允许零条测量。IIN 观测存储必须有界；当前任务窗口发生丢失必须返回溢出错误，不能用不完整聚合值继续判定。

### 12.9 质量、时间和命令状态

质量矩阵至少分开建模：Binary、Double-bit Binary、Binary Output Status、Counter、Analog、Analog Output Status。按各对象正式定义覆盖：

- `ONLINE`、`RESTART`、`COMM_LOST`、`REMOTE_FORCED`、`LOCAL_FORCED`。
- Binary 的状态/抖动过滤位，Double-bit 的四态值。
- Counter 的 rollover/discontinuity。
- Analog 的 over-range/reference error。
- 未定义/保留位原样保存并标异常，不能丢弃。

时间矩阵至少覆盖：48 位 DNP3 时间、绝对时间、相对时间与 CTO、同步/不同步时间、时间溢出和无时间对象。内部统一保存原始毫秒值；Python 再提供 UTC datetime 视图。不得在无显式时区依据时转换成本地时间。

命令结果不能压缩成布尔值。IEEE 1815-2012 Table 11-7 的 Command Status 必须按下表建模：

| 数值 | 2012 规范名称 |
|---:|---|
| 0 | `SUCCESS` |
| 1 | `TIMEOUT` |
| 2 | `NO_SELECT` |
| 3 | `FORMAT_ERROR` |
| 4 | `NOT_SUPPORTED` |
| 5 | `ALREADY_ACTIVE` |
| 6 | `HARDWARE_ERROR` |
| 7 | `LOCAL` |
| 8 | `TOO_MANY_OBJS` |
| 9 | `NOT_AUTHORIZED` |
| 10 | `AUTOMATION_INHIBIT` |
| 11 | `PROCESSING_LIMITED` |
| 12 | `OUT_OF_RANGE` |
| 13～125 | `RESERVED` |
| 126 | `NON_PARTICIPATING` |
| 127 | `UNDEFINED` |

host/Python 边界必须同时提供 2012 规范视图、后端解码数值、后端名称和线上原值是否无歧义。OpenDNP3 3.1.2 对 13～18 暴露后续修订版枚举时，只能把该别名放在 `status_backend`，2012 视图仍为 `RESERVED`。固定栈会把未识别线上值 19～125 折叠成 127；这种结果必须标记 `status_wire_raw_unambiguous=false`，不能伪称保留了原始 7-bit 值。完整原值支持需要 raw decoder 或经批准的协议栈补丁。

安全判定不得只使用 task summary：点级 `TIMEOUT`、2012 `RESERVED` 或 decoded 127 歧义均不能证明设备未执行，必须置 `execution_uncertain=true` 并进入人工读回流程。每个点还必须按 request ordinal/type/index 与原请求一一关联；缺失、重复或错配属于不确定的协议损坏。

### 12.10 Class、事件和主动上送

必须覆盖：

- Class 0 静态数据和 Class 1/2/3 事件数据。
- `ASSIGN_CLASS` 的全对象、范围和指定索引。
- 多种对象共享一个 Class 请求。
- 事件缓冲容量、溢出 IIN、事件顺序、重复事件和时间戳。
- 启动时禁止/启用主动上送的可配置策略。
- 空主动响应握手、`UNS` 位、序号、`CON` 和 CONFIRM。
- Solicited 与 unsolicited 同时发生时的排队、关联和去重。
- 总召前/后的事件策略必须按 DUT Profile 判断，不能硬编码一种厂商行为。

### 12.11 文件、Data Set、Virtual Terminal 和配置管理

这些能力不能只实现对象解析器；必须实现完整事务状态机：

| 能力族 | 最低端到端场景 |
|---|---|
| File Transfer | 认证、打开、读、写、分块、状态、重试、关闭、中止、删除、信息查询、零长度、边界长度和异常会话 |
| Data Set | prototype、descriptor、交换/读取、present value、snapshot event、类型/长度校验和 Class/事件行为 |
| Virtual Terminal | 输出块、事件数据、长度边界、会话/顺序、异常字符和流控策略 |
| Application/Config | initialize/start/stop application、application ID、activate config、activation status 和失败恢复 |
| Device Metadata | Group 0 属性、Device Storage、Device Profile 的读取、写入适用性和未知属性处理 |

文件路径必须经过规范化并限制在测试沙箱目录内，禁止 `..`、绝对路径逃逸、设备路径和符号链接逃逸。

### 12.12 Secure Authentication v5

SAv5 必须作为独立安全子项目，覆盖功能码 `0x20`、`0x21`、`0x83` 以及：

| 对象 | 名称 |
|---|---|
| G120V1 | Challenge |
| G120V2 | Reply |
| G120V3 | Aggressive Mode Request |
| G120V4 | Session Key Status Request |
| G120V5 | Session Key Status |
| G120V6 | Session Key Change |
| G120V7 | Error |
| G120V8 | User Certificate |
| G120V9 | Message Authentication Code |
| G120V10 | User Status Change |
| G120V11 | Update Key Change Request |
| G120V12 | Update Key Change Reply |
| G120V13 | Update Key Change |
| G120V14 | Signature |
| G120V15 | Update Key Change Confirmation |
| G121V0/V1 | Security Statistics |
| G122V0/V1/V2 | Security Statistic Event |

实现要求：

- 从正式标准固定算法、长度、角色、序号、挑战原因和状态码。
- 使用公司批准的密码库和系统安全随机数；禁止自研 AES、HMAC、密钥封装或随机数发生器。
- 长期密钥、更新密钥和会话密钥不得写入日志、PCAP 注释、异常消息或普通配置文件。
- 使用受控密钥存储，内存中最小化驻留并安全清理。
- 覆盖关键/非关键 ASDU、challenge-response、aggressive mode、重放、过期、MAC 错误、用户禁用、密钥更新、会话重建和安全统计。
- 必须通过标准测试向量、安全代码审查、独立实现互操作和负向测试；普通功能测试不能替代安全验证。

---

## 13. OpenDNP3 3.1.2 能力边界与补齐策略

### 13.1 不能只看枚举

“能识别 Group/Variation”不等于“主站能生成请求并完成业务事务”。能力评估必须分别检查：

1. 编解码器是否支持。
2. Master 公共 API 是否能发起。
3. 回调是否能交付完整字段。
4. 是否有正确状态机。
5. 是否有互操作证据。

### 13.2 初始缺口表

以下表格是立项初值，Agent 必须以固定的 3.1.2 源码和官方功能页复核后写入 `opendnp3_gap_analysis.md`：

| 能力 | 未修改 OpenDNP3 初始判断 | 项目处理 |
|---|---|---|
| 常用 BI/DBBI/BOS/Counter/Analog/AOS 静态与事件 | 大部分支持 | `OpenDnp3Backend` 优先交付 |
| Class 0/1/2/3 扫描和主动上送 | 支持 | 优先交付并做时序测试 |
| CROB、Analog Output，SBO/Direct Operate | 支持 | 优先交付；保留每点状态 |
| TCP、TLS、UDP、Serial | 支持通道 API | 按项目承载逐一验证 |
| Group 50 Variation 1 的读取、Variation 2，以及 Group 80 的部分主站读取路径 | 功能说明/固定枚举显示缺口或限制 | 源码确认后扩展或替换后端 |
| Self-address、广播 | 部分或缺失 | 独立链路扩展；不得伪装支持 |
| Group 0 Device Attributes | 部分/缺失 | 扩展后端和逐属性矩阵 |
| Group 31、33 Frozen Analog | 3.1.2 公共支持不足 | 扩展编解码和主站交付路径 |
| Group 34 Deadband | 缺失 | 扩展 WRITE/READ 和解析 |
| Group 13、43 Command Event | solicited/unsolicited 回调归一化已接入 | 保持 `IMPLEMENTED_UNVERIFIED`；补各值宽/时间变体 golden vector 和独立互操作，只有测试发现真实 codec/API 缺口时才扩展后端 |
| Group 110、111 Octet String | 支持 | 补长度边界和性能测试 |
| File Transfer / Group 70 | 没有完整业务实现 | 新状态机或其他后端 |
| Data Set / Groups 83、85–88 | 未实现 | 新模块或其他后端 |
| Virtual Terminal / Groups 112–113 | 未实现 | 新模块或其他后端 |
| SAv5 / Groups 120–122 | 未实现 | 独立安全模块/合格后端 |

### 13.3 后端选择决策门

在开发 OpenDNP3 缺口前，技术负责人必须比较：

- 内部 fork 的实现成本、测试成本和未来安全维护责任。
- 可采购/可替换协议栈的 API、许可证、Windows 支持、源码可得性和 2012 一致性证据。
- 是否能在统一 `IMasterBackend` 后保持 Python API 和用例不变。

只有评审记录批准后才可开始 `ExtendedOpenDnp3Backend`。涉及 SAv5 时必须额外经过密码与产品安全评审。

### 13.4 Raw 后端边界

`RawDnp3Backend` 用于协议健壮性、缺口原型和黄金帧测试，但必须：

- 使用独立连接，不和 OpenDNP3 共享 socket。
- 将链路、传输和应用编解码分层，禁止在测试用例里拼十六进制字符串。
- 正常路径的编码器默认只能生成合法帧；非法帧由显式 `FaultPlan` 在某层单点变异。
- 每个变异记录原始帧、变异位置、预期行为和实际行为。
- 只有在完整状态机和互操作测试完成后，才能把 Raw 原型提升为正式功能后端。

---

## 14. 分阶段实施计划

### 14.1 执行纪律

针对能力一般的代码 Agent，必须遵循：

- 一次对话只发一个任务卡；一个任务卡以 0.5～2 个工程日可完成为宜。
- 一个任务卡只允许一个主要结果，通常修改不超过 8 个生产代码文件。
- 每个任务卡明确输入文件、禁止事项、验收命令和需更新的能力 ID。
- Agent 先报告已阅读的固定版本 API/示例，再写代码。
- 阶段门禁未通过，不发下一阶段任务。
- 发现标准不清、API 不存在或测试环境缺失时，状态改为 `BLOCKED`，不得“合理猜测”。
- 所有协议新增功能按“黄金字节测试 → 后端实现 → Python API → 独立互操作 → 能力矩阵”顺序闭环。

里程碑编号用于能力分组，不是要求所有工作严格串行。最小依赖图为：

```text
M0 输入/目录 ──> M1 工程骨架 ──> M2 核心读路径
                                      ├──> M3 控制/服务（按风险独立拆卡）
                                      ├──> M4/H08 本机 capture 与性能工具
                                      │        └──> H09 本机 runner
                                      │                 └──> 目标环境正式 24h 证据
                                      └──> M5 经典对象/后端缺口
M6 高级事务和 M7 SAv5 仅在各自前置输入、安全评审到位后启动
```

因此 H08/H09 的本机 collector、runner、确定性回环和资源采样可以现在开发，不必等待 Restart/Freeze/Assign Class 全部完成；正式 EMS 阈值、设备资源和 24 小时结论仍必须在目标内网独占环境执行。

### 14.2 M0：输入与范围基线

任务：

1. 收集 IEEE 1815-2012 正式文本访问方式、适用勘误和 DNP3 Device Profile 模板。
2. 分别收集 EMS 的 Device Profile、PICS、批准操作约定、点表、链路地址、承载方式、最大分片、超时、主动上送策略和安全能力，并执行冲突检查。
3. 固定 OpenDNP3 3.1.2 源码 commit、SHA-256、许可证和内部镜像。
4. 从正式标准建立完整 `capability_matrix.csv`；本指导书中的表只用于交叉检查，公共矩阵的 DUT 状态保持 `UNKNOWN`。
5. 建立逐条 requirement catalog，覆盖 `shall`、`shall not` 和条件性要求，并与能力 ID、测试 ID、证据关联。
6. 编写 `opendnp3_gap_analysis.md`，逐项引用固定源码/API。
7. 明确参考从站、独立主/从站工具、一致性测试资源和抓包位置。

验收：

- 能力矩阵校验脚本通过。
- 所有功能码、对象/变体、限定词、IIN、质量、链路/传输、安全条目均已登记，且规范性要求目录不存在未解释的遗漏。
- EMS PICS 中的 `SUPPORTED`、`NOT_SUPPORTED` 和 `UNKNOWN` 可机器读取。
- 架构、缺口和许可证评审均有负责人签字/电子记录。

停止条件：缺少正式标准时可以进入 M1 工程骨架，但 M2 以后任何未从源码/标准确认的协议行为保持 `BLOCKED`；缺少 DUT Profile 时不能设计最终断言。

### 14.3 M1：可运行工程骨架

任务：

1. 创建 CMake、CMakePresets、C++ 单测、Python 包和 pytest 配置。
2. 实现严格 NDJSON 解析、`hello`、`get_status`、`shutdown`。
3. 实现 Python 子进程管理、超时、stderr 捕获、异常退出和幂等清理。
4. 加入 JSON Schema、请求行大小限制、日志配置和 build-info。
5. 建立 Windows CI/离线构建脚本。

验收：

- Release 构建、C++ 单测和 Python 单测全部通过。
- 连续 1,000 次启动、`hello`、`shutdown` 无僵尸进程和句柄持续增长。
- 空行、非法 UTF-8、非法 JSON、重复 ID、未知命令、超长行都有稳定错误码，进程不崩溃。
- stdout 的每一非空行均能被 JSON 解析；普通日志只在 stderr。
- 尚未实现的已知命令统一返回 `UNSUPPORTED_BY_BACKEND`，未知命令返回 `INVALID_REQUEST`，不能返回假数据或引入未登记错误码。

### 14.4 M2：OpenDNP3 核心读路径

任务：

1. 按 3.1.2 官方 master 示例实现 Manager → Channel → Master 生命周期。
2. 实现 TCP client 首个承载；随后按项目需求逐个加入 Serial、UDP、TLS。
3. 实现连接状态事件、一次性总召、Class 0/1/2/3 扫描、范围读取和多 Header 读取。
4. 交付 OpenDNP3 已支持的 BI、DBBI、BOS、Counter、Frozen Counter、Analog、AOS 和 Octet String。
5. 完整上送索引、值、flags、时间、来源（solicited/unsolicited）、分片信息和 task ID。
6. 返回原始 IIN 与解析 IIN，记录任务开始/完成状态。

验收：

- 至少与一个非本项目实现的参考从站互通，保存 PCAP 和测试报告。
- 每个已交付对象变体都有黄金字节解析测试和端到端测试。
- 总召、Class 扫描、指定范围、多 Header、空响应、多分片响应、断线中断均有测试。
- `hello.capabilities` 只暴露实测支持项。
- 断开和销毁顺序经过重复连接测试，无回调访问已销毁对象。

### 14.5 M3：控制、事件和服务功能

任务按以下顺序拆成独立任务卡：

1. CROB 的 Select-Before-Operate 和有响应 Direct Operate；No Response 只有在固定 API/扩展后端明确支持时实施，否则登记缺口。
2. 4 种 Analog Output Command 变体和批量命令。
3. Enable/Disable Unsolicited、空主动响应、事件主动上送和 Confirm。
4. 时间同步：Delay Measure/Write Time，以及 Record Current Time 流程。
5. Cold/Warm Restart 和重启延时。
6. Immediate Freeze、Freeze-and-Clear、Freeze-at-Time 及 No Response 版本。
7. Assign Class 和可配置周期扫描。

验收：

- 每点返回完整 Command Status；混合成功/失败不得折叠为整个批次布尔值。
- SBO 覆盖未 Select、Select 超时、Select/Operate 值不一致、重复 Operate。
- No Response 命令通过抓包、后读回和 DUT 副作用验证。
- 时间偏差断言包含采集端延迟预算，保存 DUT 前后时间和测量结果。
- 主动上送覆盖序号回绕、重发、Confirm 丢失、Solicited 交错和断线重连。
- 重启/冻结/遥控测试只能在经授权的实验 EMS 上运行，并有 pytest 风险标记。

### 14.6 M4：大数据量与性能路径

M4 不得作为一张大任务卡实施。它在 M2 的稳定 Read/unsolicited 数据路径和 Capture v1 设计评审完成后即可独立启动，不以 M3 的 Restart/Freeze/Assign Class 为前置。按内部依赖顺序拆分：

| 任务卡 | 唯一产出 |
|---|---|
| `T15a` | Capture v1 状态机、字段、错误语义、Schema 和指标词典评审完成；不写 collector 业务代码 |
| `T15b` | native `MeasurementStore`、三种 mode、有界内存和确定性单元测试 |
| `T15c` | host `capture.begin/progress/end`、幂等、超时/溢出/断开清理和协议测试 |
| `T15d` | Python 类型化 API、fake host、真实 native 回环和打包合同测试 |
| `T16a` | 可扩展本地参考从站/发生器、确定性 seed 和外部真值清单 |
| `T16b` | Windows host 资源采样、指标 source/scope 和 capture A/B 开销测试 |
| `T16c` | `performance_profile.json`、报告模型、阈值判定和短时 benchmark/soak |
| `T16d` | 24 小时 runner、watchdog、原子检查点、轮转和中断证据测试 |
| `T16e` | 在独立参考端和目标内网环境正式执行；只产生环境证据和阈值结论，不顺带修改协议能力 |

总体任务：

1. 实现 `MeasurementStore` 的 detail/summary 模式和有界队列。
2. 按第 10 节实现 `capture.begin/progress/end`、三种 capture mode、期望模型、样本上限、队列水位和断开清理。
3. 复用现有多 Header `read` 和多点 `direct_operate`/`select_and_operate`，在 C++ 内聚合；只有现有线上语义无法表达时才新增批量命令。
4. 使用单调时钟记录 task submit、first fragment、first object、last object 和 task completion；wire first byte 仅由批准的底层插桩或 PCAP 提供。
5. 增加带 source/scope 的 CPU、working set、private bytes、句柄、线程、队列深度统计；网络字节和 DUT 资源不可采集时返回 `null`，不得估算。
6. 实现可重复数据发生器、外部真值清单、性能 Profile、短时 soak 和 24 小时 runner。

验收：

- 先用参考从站测出工具自身上限；工具上限应高于项目 EMS 目标并保留安全余量，具体阈值写入项目配置而非源码。
- 目标点数下所有 overflow/drop 为 0；有真值的场景 `missing == 0`，无真值场景明确为 unknown，且内存/句柄/线程趋势满足 Profile 阈值。
- summary 模式不会生成逐点 JSON；响应大小有上限。
- 至少覆盖：大总召、突发事件、持续事件、批量控制、断线重连后补事件、24 小时稳定性。
- 报告给出带统计总体和 source/scope 的 p50/p95/p99/max、吞吐、完整性、工具资源，以及由外部监控实际提供的 DUT 资源；不能只给平均值或伪造不可用指标。

### 14.7 M5：经典对象与后端缺口

任务按能力族拆分：

- Group 0 Device Attributes。
- Group 31/33 Frozen Analog，Group 34 Deadband。
- Group 13/43 Command Event 的定向变体测试和独立互操作；现有回调映射不是待重写项，除非测试证明存在真实缺口。
- Group 50 Variation 1 的读取、Variation 2，以及 Group 80 主站读取路径。
- `DIRECT_OPERATE_NR` 和公共 `Header` 无法表达的控制/限定词组合。
- 广播、self-address 和 OpenDNP3 无法表达的限定词/请求组合。
- 2012 能力矩阵中其他“编解码支持但公共 Master API 不完整”的条目。

实施方式：优先扩展独立编解码/后端层；修改 OpenDNP3 时保持小型补丁集，补上游风格测试并记录 fork commit。每个能力先证明公共 API 的具体缺口，不能因为不会用 API 就重写协议。

验收：

- 每个新增对象有 encode/decode 黄金字节、范围/限定词、错误对象和多分片测试。
- 与独立实现完成双向互操作；只用内部 fork 两端互测不算通过。
- `OpenDnp3Backend` 与 `ExtendedOpenDnp3Backend` 的能力声明准确区分。
- Windows Debug/Release、静态分析和 sanitizer 可用环境全部通过。

### 14.8 M6：高级应用对象和事务

按独立模块实施：

1. File Transfer / Group 70。
2. Data Set / Groups 83、85–88。
3. Virtual Terminal / Groups 112–113。
4. Device Storage/Profile、Application ID、Activation Status。
5. Initialize/Start/Stop Application 和 Activate Configuration。
6. Group 101 BCD、Group 102 Unsigned Integer 及未覆盖的元数据对象。

每个模块先写状态机设计，包括状态、输入、超时、重试、幂等、资源上限和异常清理，再写代码。

验收：

- 不只解析单帧；最低端到端场景全部通过。
- 断线、重复响应、错误 handle/block number、超时和中止能回收资源。
- 文件、Data Set、VT 输入有大小/数量/路径限制和模糊测试。
- 对 EMS PICS 声明不支持的项目，能验证标准规定的不支持行为。

### 14.9 M7：Secure Authentication v5

前置条件：安全负责人批准设计、密码库和密钥生命周期；取得 2012 正式条款、匹配测试向量及独立互操作端。

任务：

1. 编解码 G120–122 和认证功能码。
2. 实现用户、更新密钥、会话密钥、安全序号、挑战及状态机。
3. 集成批准的密码库和安全随机数，建立密钥存储抽象。
4. 实现 critical ASDU 策略、challenge-response 和 aggressive mode。
5. 实现安全统计、审计事件和无秘密日志。
6. 加入重放、篡改、过期、错误用户、密钥轮换和断连负向测试。

验收：

- 全部目标算法的正式/批准测试向量通过。
- 与独立 SAv5 实现互操作。
- 密钥不出现在 stdout、stderr、崩溃转储、报告、PCAP 注释和普通配置。
- 安全代码审查、威胁模型和依赖漏洞审查完成。
- 未满足任一前置条件时保持 `BLOCKED`，禁止自行实现密码原语。

### 14.10 M8：Raw 负向、健壮性和模糊测试

任务：

- 为链路、传输、应用头、对象头、限定词、长度、CRC、序号和时间分别建立单点 `FaultPlan`。
- 增加基于语法的生成测试与覆盖引导 fuzzing；解析器入口必须可在无网络条件下运行。
- 测试慢速发送、超大声明、连接抖动、重复/乱序/缺失、未知功能码/对象和保留位。
- 记录 EMS 的响应、IIN、断连、资源使用和恢复时间。

验收：

- 每个负向用例只有一个主变量，预期行为可解释。
- 测试工具自身在 sanitizer、静态分析和 fuzz corpus 回归中无崩溃、越界、泄漏和死锁。
- EMS 异常不得把框架异常误报为 DUT 缺陷；每个失败保留最小复现帧和 PCAP。
- Fuzzing 不能连接生产或未经授权设备。

### 14.11 M9：互操作、一致性与发布

任务：

1. 对 DUT PICS 生成适用测试集和不适用清单。
2. 与至少一个独立协议实现执行互操作回归。
3. 按项目取得的 DNP3 一致性程序执行适用用例。
4. 汇总能力矩阵、失败项、偏差、PCAP、版本、配置和环境。
5. 生成 SBOM、第三方许可证、签名发布包和可复现构建说明。

验收：

- 本次宣称范围内没有 `NOT_ANALYZED`、`PLANNED` 或 `IMPLEMENTED_UNVERIFIED`。
- 所有 `VERIFIED_*` 均能定位到不可变证据和构建版本。
- 所有偏差有批准的 waiver、有效期和风险说明。
- 新机器按文档可离线构建、运行 smoke test 并复现代表性用例。
- 发布报告只陈述逐项证据，不使用未经授权的“IEEE/DNP3 认证”措辞。

---

## 15. 测试与证据体系

### 15.1 测试层级

| 层级 | 目标 | 是否需要真实网络 |
|---|---|---|
| C++ unit | 编解码、状态机、队列、边界、错误映射 | 否 |
| IPC contract | NDJSON Schema、超时、进程生命周期 | 否 |
| Component | 后端与受控参考从站 | 是/loopback |
| Interoperability | 与独立实现或真实 EMS | 是 |
| Conformance | 正式测试程序的适用项 | 是 |
| Robustness/Fuzz | 非法输入、资源限制和恢复 | 可选网络 |
| Performance/Soak | 吞吐、完整性、延迟、稳定性 | 是 |

### 15.2 黄金向量规则

- 黄金字节必须由正式表、批准的测试程序或独立工具生成并人工复核，不能从被测编码器自我导出后直接固化。
- 每个向量包含 `vector_id`、能力 ID、标准引用、方向、完整字节、字段解释、预期结果和来源。
- PCAP 不是唯一断言源；测试还要验证结构化结果和 DUT 可观察状态。
- Wireshark 仅用于辅助检查，不能代替正式标准或一致性工具。
- 修改黄金向量必须单独评审，禁止为让失败测试变绿而顺手更新。

### 15.3 独立性要求

最低证据组合：

1. 本项目字段级单元测试。
2. OpenDNP3 master 与非 OpenDNP3 outstation，或扩展后端与独立实现。
3. 真实 EMS 适用项。
4. 正式一致性程序可用时的适用结果。

同一协议栈的 master/outstation 两端互通只能算回归测试，不能作为唯一互操作证据。

### 15.4 PCAP 和运行清单

当前 pytest 插件实际生成 `<evidence-root>/<run_id>/manifest.json` 和带哈希的 `pytest-results.json`。清单结构以 `schemas/evidence-manifest.schema.json` 为准，简化示例如下：

```json
{
  "run_id":"pytest-20260828T120000000000Z-a1b2c3d4",
  "state":"COMPLETED",
  "started_at_utc":"...",
  "completed_at_utc":"...",
  "pytest_exit_status":0,
  "runner":{
    "python_implementation":"CPython",
    "python_version":"...",
    "platform":"...",
    "pytest_version":"..."
  },
  "source_control":{"available":true,"commit":"...","tracked_worktree_state":"clean"},
  "build_info":{"host_version":"0.6.1","opendnp3_version":"3.1.2"},
  "inputs":{
    "pics":{"present":true,"file_name":"ems.local.json","size_bytes":1234,"sha256":"..."},
    "points":{"present":true,"file_name":"points.local.csv","size_bytes":2345,"sha256":"..."}
  },
  "results":{"file_name":"pytest-results.json","sha256":"...","test_count":12,"phase_count":36},
  "redaction":{
    "private_input_contents_stored":false,
    "absolute_input_paths_in_metadata_stored":false,
    "known_absolute_paths_redacted":true,
    "arbitrary_test_output_requires_review":true,
    "report_text_limit_chars":16384,
    "sensitive_fields_redacted":true
  }
}
```

当前 recorder 已覆盖源码、构建、私有输入摘要、pytest 结果和脱敏边界，但尚未为独立对端身份、PCAP、测试机硬件、电源计划、性能真值和指标 source/scope 提供专用强类型字段。H09/M9 在形成正式互操作或性能结论前必须扩展证据 Schema（或绑定经批准且带哈希的独立元数据文件），不能仅把这些信息写在自由文本里。所有报告、PCAP、配置快照、日志和矩阵快照计算 SHA-256；能力矩阵中的 `VERIFIED_*` 引用还必须追加 `#sha256=` 并由校验器复算。清单及周期检查点采用原子写入；敏感配置先脱敏，密钥材料永不进入证据包。

### 15.5 测试命名和标记

测试 ID 推荐：

```text
TC_<LAYER>_<CAPABILITY>_<SCENARIO>_<NNN>
TC_APP_FC01_G30V1_RANGE_001
TC_APP_FC03_FC04_CROB_SELECT_TIMEOUT_001
TC_LINK_CONFIRMED_DUPLICATE_FCB_001
TC_SEC_G120V1_REPLAY_001
```

当前 `python/pyproject.toml` 在 `--strict-markers` 下注册的项目标记为：

```text
@pytest.mark.dnp3_capability("OBJ.G30.V1")
@pytest.mark.dnp3_dut
@pytest.mark.dnp3_unsupported_behavior  # 仅“不支持行为”测试使用
@pytest.mark.dnp3_state_changing        # 仅可能改变 DUT 的测试使用
@pytest.mark.lifecycle_stress           # 仅本机生命周期压力测试使用
```

不需要真实 DUT 的普通单测不加 `dnp3_dut`。改变状态、重启、删除文件、激活配置等测试必须使用 `dnp3_state_changing`，并由命令行显式授权和校验实验环境标识。H09 如要新增 `performance`/`soak` 标记，必须先在 `python/pyproject.toml` 注册并补严格标记测试；不得直接复制尚未注册的示例标记。

### 15.6 性能场景参数

不得在代码中写死“合格点数/时延”。在 `performance_profile.json` 中定义：

- 每类测点数量及索引分布。
- Capture mode、来源过滤、紧凑期望点集；事件场景还要绑定外部真值/发生器 seed、清单哈希和匹配规则。
- 静态总召总点数和响应分片策略。
- 事件突发量、持续速率、变化模式和事件 Class。
- 命令批大小、发送间隔和并发策略。
- 预热、测量、冷却、轮次数和稳定性时长。
- 完整性要求、允许重复策略、p95/p99/最大时延阈值。
- 工具 CPU/内存/队列上限和安全余量。
- 指标 source/scope、资源采样周期、nearest-rank 百分位统计总体和最小样本数。
- Soak 检查点周期、日志/PCAP/证据总量、磁盘余量、允许重连次数、内存增长斜率及句柄/线程阈值。

结果必须同时报告发送/接收对象数、唯一点数、事件数、字节数、分片数、重复、缺失、溢出、超时和断连。不可测或不可证明的值必须为 `null` 并说明原因；只报“每秒 N 点”不足以判定性能。

---

## 16. 配置、并发、可靠性与安全

### 16.1 配置示例

```python
from pathlib import Path

from dnp3_master import HostProcessConfig, TcpConnectionConfig

host_process = HostProcessConfig(
    executable=Path("bin/dnp3-master-host.exe"),
    safety_incident_directory=Path("evidence/local/safety-incidents"),
    startup_timeout=5.0,
    request_timeout=10.0,
    shutdown_timeout=2.0,
)

connection = TcpConnectionConfig(
    host="192.0.2.10",
    port=20000,
    local_adapter="0.0.0.0",
    connect_timeout=5.0,
    retry_min=1.0,
    retry_max=30.0,
    master_address=1,
    outstation_address=10,
    keep_alive_timeout=60.0,
)
```

当前 0.6.1 没有一个可执行的 `config/default.json` 或总配置 Schema；不要按旧草图创建它。pytest 运行参数由插件 CLI/环境变量生成 `HostProcessConfig` 和 `TcpConnectionConfig`，DUT 能力、点表和业务场景分别使用 `ems.local.json`、`points.local.csv`、`ems_test_plan.local.json`，格式参照 `config/*.example.*`；性能/soak 和本机事件发生器分别使用 `performance_profile.local.json` 与 `local_event_profile.local.json`。示例地址 `192.0.2.0/24` 是文档用途；实际配置由环境注入。控制会话另附 `LabSafetyConfig`，其中 `operator_id` 和 `dut_id` 必须来自批准记录。Python 层还必须配置可靠的持久事故锁目录。证书私钥、口令和 SAv5 密钥只写引用 ID，不写明文。

### 16.2 线程和队列模型

建议线程职责：

- OpenDNP3 自有 I/O/executor 线程：执行协议回调。
- `HostController` 命令线程：串行处理 v1 RPC。
- Collector 工作线程：批量消费测点并聚合。
- stdout writer 单线程：保证一行响应原子化且不交错。
- stderr/logger 独立异步 sink；队列满时按策略丢弃低级日志并计数。

锁顺序必须文档化。回调不得等待命令线程；命令线程等待协议任务时使用有超时的 future/promise。断开时先停止接收新任务，再取消/完成在途任务，禁用 master，关闭 channel，停止 collector，最后销毁 manager。

### 16.3 超时和取消

必须区分本地调用期限、host 错误和设备返回结果：

- Python 未在本地期限内得到 host 响应：抛出客户端 `HostTimeoutError`，立即回收 host；这不是一个伪造的 NDJSON `RPC_TIMEOUT`。
- TCP 未在连接期限内 OPEN：host 返回 `CONNECTION_TIMEOUT`，并保证本次创建的资源已清理。
- Read/控制线上任务没有在期限内得到有效响应：host 返回 `RESPONSE_TIMEOUT`。
- OpenDNP3 任务或命令点明确失败：返回 `TASK_FAILED`/`COMMAND_FAILED`，并保留 IIN、TaskCompletion 和逐点 Command Status。
- Capture 自身 deadline：进入 `TIMED_OUT` 终态并返回 `valid=false`，不冒充 DNP3 响应超时。

任何状态改变请求只要出现 host `RESPONSE_TIMEOUT`、提交异常、客户端超时/进程退出/协议损坏、请求/结果关联异常、`execution_uncertain=true`、`may_still_execute=true`，或点级 `TIMEOUT`/2012 保留值/decoded 127 歧义，Python 必须清除安全令牌、销毁 host、为 DUT 写入跨进程持久事故锁并禁止后续控制。事故锁只能在新的只读会话完成独立读回、保存证据并显式确认准确 incident ID 后归档。事故锁写入失败仍按 fail-closed 停止全部控制。任何层都不得自动重发或自动发送恢复命令；完整流程以 `docs/SAFETY_INCIDENT_RUNBOOK.md` 为准。

### 16.4 输入与资源限制

所有限制可配置且有安全默认值：请求行、JSON 深度、Header 数、点数、索引、响应大小、事件缓存、队列、文件大小、Data Set 元素数、VT 块、会话数、日志大小和 PCAP 大小。当前 Read 的逐点测量由 `max_measurements` 限制，分片记录最多 4096 条，IIN 观测最多 1024 条；任一当前任务数据丢失都必须返回 `QUEUE_OVERFLOW`。

解析规则：

- 拒绝重复 JSON key、非有限数值、错误类型、未知必填枚举和整数越界。
- 不接受 JSON 中以浮点表达的 64 位时间/索引。
- 路径、证书引用、串口名、主机和端口分别校验。
- 错误消息不得回显秘密或无限长度输入。

### 16.5 安全操作门

会改变 EMS 状态的命令必须同时满足：

1. 配置 `environment == "LAB"`。
2. 相应 `allow_*` 明确为 true。
3. pytest 用例带正确风险标记。
4. `operator_id`、`dut_id`、授权工单和目标点/动作范围准确绑定。
5. 命令请求携带本会话一次性签发并绑定 DUT/操作者的短期 `safety_token`；disconnect、进程退出或不确定结果后立即失效。
6. 同一 DUT 没有未解决的持久事故锁，且控制测试在所有主机/worker 间串行。
7. 运行清单记录操作者、DUT、唯一场景、批准值、反馈点、恢复值和停止条件。

默认拒绝广播控制、重启、文件删除、配置激活、未知地址控制和未显式选择的批量控制性能场景。操作、反馈读回或恢复任何一步不确定时立即停止，不得继续批次或猜测设备状态。框架不得把“测试主站”当作绕过 EMS 权限控制的工具。

### 16.6 日志和可观测性

结构化日志至少含 UTC 时间、level、component、session_id、task_id、request_id、channel state 和错误码。协议原始字节按独立受控选项记录；默认不记录认证对象和秘密。

必须提供：

- `get_status`：状态、在途任务、连接、队列、资源摘要。
- `stats`：v1 的唯一指标命令，返回累计连接、任务、超时、对象、可用字节指标、溢出和日志丢弃，并为每项声明 source/scope；不要另造不兼容的 `get_metrics` 别名。
- `capture.progress`：返回当前 capture 的有界、非消费、只读快照；终态由
  `capture.end` 固化，同一 capture ID 的 end 在下一次 begin 前保持幂等。
- `dump_diagnostics`：后续候选命令；只有加入命令注册表、Schema 和 capability 声明后才是“已知命令”，届时提供有大小上限的非秘密诊断快照。注册前仍按未知命令返回 `INVALID_REQUEST`。
- 崩溃转储策略：实验环境可开启，但 SAv5 场景需安全批准和受控存储。

---

## 17. 完成定义、发布与维护

### 17.1 单个任务卡完成定义

同时满足以下条件才算完成：

- 需求中的能力 ID 和标准引用明确。
- 生产代码无空实现、假返回或未登记 TODO。
- 正向、边界和至少一个负向测试通过。
- Windows 目标构建和受影响测试实际执行，不只给命令建议。
- 没有 stdout 污染、线程泄漏、句柄泄漏或未受控队列增长。
- JSON Schema、Python 类型和 C++ 模型同步更新。
- 文档、能力矩阵和证据路径更新。
- 代码审查者可以从报告复现结果。

### 17.2 常用功能版本完成定义

可以发布“OpenDNP3 常用功能版本”的条件：

- 发布 Profile 明确列出本次承诺的能力 ID、使用场景和证据等级；这些能力全部通过对应阶段门禁。
- `hello.capabilities` 和发布说明明确列出缺失项。
- 所有未支持能力返回稳定的 `UNSUPPORTED_BY_BACKEND`。
- 如果本次 Profile 声称性能/大点表/稳定性能力，则相应 H08/H09 门禁必须通过；不声称时允许仍为缺口，但已有队列溢出必须可检测且不得静默成功。
- 发布包在干净 Windows x64 主机通过 smoke test。

允许表述：

> 本版本面向 IEEE 1815-2012 DNP3 主站自动化测试，已验证的能力范围见随附 capability matrix；高级对象和 SAv5 的状态以该矩阵为准。

禁止表述：

> 本版本完整实现/已认证 IEEE 1815-2012。

### 17.3 全范围版本完成定义

只有同时满足以下条件，才能由公司合规/技术负责人评审“全范围测试能力”表述：

- IEEE 1815-2012 能力目录和逐条 requirement catalog 均没有遗漏，且完成双人标准复核。
- 所有分配功能码和对象/变体具有至少编码、解析或“不适用于主站”的正式依据。
- 所有主站适用能力达到批准的证据级别。
- OpenDNP3 缺口已由扩展/替换后端真实补齐，而不是只存在 API。
- SAv5 完成安全评审、测试向量和独立互操作。
- 目标 DUT PICS 的全部适用项有结果，不适用项有依据。
- 通过项目要求的独立互操作和正式一致性测试。

即便满足这些条件，框架“覆盖标准能力”和某一台 EMS“符合标准”仍是两个不同结论，必须分开出报告。

### 17.4 版本策略

- Host、Python client、JSON Schema、能力矩阵分别版本化。
- 协议不兼容变更提升 `schema_version`；旧 client 收到新主版本必须明确拒绝。
- 后端能力通过 `capability_id + implementation_revision` 标识。
- 每次发布固定源代码、依赖、构建工具、标准版本和证据矩阵快照。
- IEEE 1815 后续版建立新 edition 分支/矩阵，不能覆盖 2012 条目。

### 17.5 OpenDNP3 长期维护

OpenDNP3 上游仓库已经归档，不能假设会继续获得缺陷和安全修复。项目必须：

- 设置内部代码所有者和至少两名可维护 C++/DNP3 的工程师。
- 保留可重建的上游 3.1.2 基线和最小补丁序列。
- 对协议栈、ASIO、TLS/密码库和 JSON 库持续做漏洞跟踪。
- 每次工具链升级运行全量黄金向量、互操作和性能回归。
- 补丁按功能拆分，禁止形成无法审计的大型一次性 fork。
- 每年至少进行一次恢复构建和维护演练，避免只有原开发者能发布。

---

## 18. 如何向内网 Agent 下发任务

### 18.1 固定上下文

每次会话先附上第 1 节总执行提示词，并告诉 Agent 当前任务卡编号。不要让 Agent 仅凭之前聊天记忆继续工作；要求它重新读取任务卡列出的文件。

### 18.2 任务卡模板

复制并填写：

```text
任务卡编号：<例如 T02>
所属里程碑：<M0-M9>
唯一目标：<只写一个可验收结果>

开始前必须读取：
- <文件 1>
- <文件 2>
- OpenDNP3 3.1.2 中的 <准确示例/头文件路径>
- capability_matrix.csv 中的 <能力 ID>
- IEEE 1815-2012 <内部引用位置和条款号>

允许修改：
- <目录/文件范围>

禁止事项：
- 不实现下一任务
- 不修改无关公共 API
- 不猜 OpenDNP3 API
- 不把未实现能力返回为成功
- <本任务其他限制>

实现要求：
1. <要求 1>
2. <要求 2>
3. <要求 3>

必须新增的测试：
- 正向：<...>
- 边界：<...>
- 负向：<...>

必须执行：
- <build command>
- <test command>

验收条件：
- <机器可判断条件 1>
- <机器可判断条件 2>
- capability_matrix.csv 的 <能力 ID> 更新且附证据

如果输入、标准依据或 API 不足：停止编码，按 BLOCKED 格式报告，不得猜测。
完成后严格使用项目级回复格式。
```

### 18.3 历史首卡（T00 已完成，不要重新执行）

下列 T00 仅保留为任务卡写法示例。当前仓库已经完成 T00～T12 的核心闭环；内网 Agent 必须先读取 `docs/INTRANET_HANDOFF_REMAINING_TASKS.md`，从其中尚未完成的 H01～H14 卡片选择一个，不得按本节从头重建工程。

```text
任务卡编号：T00
所属里程碑：M0
唯一目标：建立项目输入缺失清单和 capability_matrix.csv 骨架；本任务不写协议实现。

开始前必须读取：
- 本开发指导书第 0、2、3、11、12、13、14 节
- 公司内部 IEEE 1815-2012 的访问说明
- EMS Device Profile、独立 PICS 和批准操作约定（如果已提供，三者分开读取）
- OpenDNP3 3.1.2 固定源码的 README、features 文档和 master API 头文件

允许修改：
- docs/standards/
- config/capability_matrix.csv
- scripts/validate_capabilities.py
- scripts/tests/test_validate_capabilities.py

禁止事项：
- 不创建 dnp3-master-host 业务代码
- 不自行补猜标准条款号、Qualifier 数值或对象字段
- 不把 OpenDNP3 枚举存在当成主站端到端支持

实现要求：
1. 建立 inputs_checklist.md，逐项写 PRESENT/MISSING/OWNER。
2. 按本指导书表头建立能力矩阵；从正式标准导入的条目标注准确条款。
3. 对无法核对的条目标记 BLOCKED 或 REVIEW_REQUIRED，并写所缺材料。
4. 编写矩阵校验脚本及单元测试，检查 ID、枚举、引用和证据规则。
5. 建立 opendnp3_gap_analysis.md 骨架，逐项引用固定源码路径。

必须新增的测试：
- 重复 capability_id 被拒绝
- 非法状态被拒绝
- VERIFIED 状态无 test/evidence 被拒绝
- 合法最小矩阵通过

必须执行：
- python -m pytest scripts/tests -q
- python scripts/validate_capabilities.py config/capability_matrix.csv

验收条件：
- 输入缺失项清晰且未被猜测填充
- 校验脚本测试全通过
- 矩阵目录覆盖本指导书第 12 节所有能力族

如果没有正式标准文本：仍完成骨架和缺失清单，但所有需要条款核对的条目保持 BLOCKED；不得声称 M0 完成。
完成后严格使用项目级回复格式。
```

### 18.4 历史任务队列与当前入口

下表说明原始编号与能力演进，不代表当前待办。实时状态、依赖和停止条件只以 `docs/INTRANET_HANDOFF_REMAINING_TASKS.md` 为准，不要把下表合并成一个大任务：

| 顺序 | 任务卡 | 唯一产出 |
|---:|---|---|
| 1 | T00 | 输入清单、矩阵骨架和校验器 |
| 2 | T01 | Windows CMake/C++/Python 空工程可构建 |
| 3 | T02 | NDJSON `hello/shutdown` 与 Schema |
| 4 | T03 | Python 子进程包装和 1,000 次生命周期测试 |
| 5 | T04 | 固定 OpenDNP3 3.1.2 离线依赖和 build-info |
| 6 | T05 | TCP channel connect/disconnect 和状态事件 |
| 7 | T06 | 总召/Class Read 请求及任务完成模型 |
| 8 | T07 | 第一类测量对象交付；之后每个对象族独立任务 |
| 9 | T08 | 原始/解析 IIN 和错误映射 |
| 10 | T09 | CROB SBO |
| 11 | T10 | Direct Operate 与 No Response |
| 12 | T11 | Analog Output 四变体与批次结果 |
| 13 | T12 | Unsolicited 完整时序 |
| 14 | T13 | 时间同步 |
| 15 | T14 | Restart、Freeze、Assign Class；三者分别提交 |
| 16 | T15a～T15d | Capture 合同、native collector、host/Python API 与回环闭环 |
| 17 | T16a～T16d | 发生器、资源指标、性能报告和 24 小时 runner |
| 18 | T16e | 独立参考端及目标内网正式基准与稳定性证据 |
| 19 | T17+ | 按 M5 矩阵每个缺口独立实施 |
| 20 | T30+ | 按 M6 每个事务模块独立实施 |
| 21 | T40+ | 经安全批准后实施 M7 |
| 22 | T50+ | M8 逐层 FaultPlan 和 fuzz target |
| 23 | T60 | M9 发布证据与报告生成 |

### 18.5 给代码审查 Agent 的提示词

```text
你只做审查，不修改代码。目标标准固定 IEEE 1815-2012，协议栈固定 OpenDNP3 3.1.2。

请先读取任务卡、diff、相关标准引用、固定版本头文件和测试。重点检查：
1. 是否猜测了不存在/版本错误的 OpenDNP3 API。
2. 是否把枚举/解析支持误当成端到端支持。
3. APDU、对象、Qualifier、IIN、quality、time、Command Status 是否丢字段。
4. 回调是否阻塞，生命周期是否可能 use-after-free/deadlock。
5. stdout 是否可能混入日志；超时/取消后任务是否悬挂。
6. 队列溢出、整数溢出、长度/路径/内存上限是否处理。
7. 测试是否由同一实现自我证明，是否缺少黄金字节/独立证据。
8. capability_matrix 状态是否高于现有证据。
9. 是否存在改变 EMS 状态而无安全门的命令。
10. SAv5 是否出现自研密码、秘密日志或密钥生命周期缺口。

输出：BLOCKER、MAJOR、MINOR、TEST_GAP 四类问题。每条包含文件/位置、具体风险、复现或证据、最小修复建议。没有证据不要猜问题。
```

### 18.6 Agent 常见错误及纠正指令

| 错误 | 立即纠正 |
|---|---|
| 混用 OpenDNP3 2.x/3.x API | 要求读取固定 3.1.2 头文件和 example，删除猜测代码 |
| 一次生成大量文件但不编译 | 缩小到一个任务卡，要求先构建最小闭环 |
| stdout 输出日志 | 将日志全部迁移 stderr，并加协议纯净性测试 |
| 每点一条 JSON | 切换 C++ 聚合和 batch API |
| `bool success` 丢 Command Status/IIN | 恢复原始状态、每点结果和 IIN |
| OpenDNP3 缺口返回空成功 | 返回 `UNSUPPORTED_BY_BACKEND` 并更新矩阵 |
| 测试里手写大量 hex | 建立分层 builder；黄金向量仍保留独立来源 |
| Agent 根据博客补标准 | 回滚该实现，标 `BLOCKED`，提供正式条款后再做 |
| 只与本项目 outstation 互通 | 状态最多 `VERIFIED_UNIT`，安排独立互操作 |
| 为 SAv5 自写密码算法 | 立即停止，进入安全设计和批准库选型 |

---

## 19. 官方资料与版本依据

正式实现以公司合法持有的 IEEE 1815-2012 文本为最高依据。下列公开资料用于定位、工具评估和交叉检查：

1. IEEE 1815-2012 标准页面：<https://standards.ieee.org/ieee/1815/5414/>
2. OpenDNP3 归档仓库说明：<https://github.com/dnp3/opendnp3/blob/release/README.md>
3. OpenDNP3 3.0 功能表：<https://dnp3.github.io/docs/guide/3.0.0/features/features/>
4. OpenDNP3 3.0 Master API：<https://dnp3.github.io/docs/guide/3.0.0/api/masters/>
5. OpenDNP3 3.0 Channels：<https://dnp3.github.io/docs/guide/3.0.0/api/channels/>
6. OpenDNP3 3.1 C++ API：<https://dnp3.github.io/docs/cpp/3.1.0/>
7. DNP3 Application Note AN2013-004b（公开摘要表，不能替代正式标准）：<https://www.dnp.org/LinkClick.aspx?fileticket=bTubmc6O7kg%3D&forcedownload=true&mid=447&portalid=0&tabid=66>

上述功能/API 页面分别标注 3.0.0 或 3.1.0，并不是精确的 3.1.2 实现规范；它们只能辅助定位。所有 3.1.2 API、能力和默认值必须以仓库固定源码、头文件、example、测试及依赖锁哈希为准。外部页面一旦被用于证据，应在访问说明中记录访问日期和内部快照/摘要哈希，避免链接变化造成不可复现。

IEEE 页面中的标准活动状态可能随时间更新；项目如需在报告中陈述该状态，必须在线核验并记录访问日期。无论页面状态如何，本项目明确锁定 2012 版；如果未来迁移到更新版本，应走第 17.4 节的版本策略。

---

## 附录 A：错误响应和能力响应示例

0.6.0 已实现 `capture.begin/progress/end`。请求和终态必须分别满足
`schemas/request.schema.json` 与 `schemas/capture-result.schema.json`；静态点集、
事件摘要和纯观察三种模式的完整示例见 `docs/protocol.md`。同一会话同时只允许
一个 ACTIVE capture，ID 错配或并发 begin 返回 `INVALID_STATE`，队列溢出返回
`QUEUE_OVERFLOW` 并在 `error.details.operation_result` 保留可审计终态。

能力响应不能只返回一个 `supports_all`：

```json
{
  "schema_version":1,
  "id":"req-1",
  "ok":true,
  "result":{
    "host_version":"0.6.1",
    "backend":"opendnp3",
    "backend_version":"3.1.2",
    "git_commit":"...",
    "platform":"windows-x64",
    "capability_matrix_version":"1",
    "capability_matrix_sha256":"...",
    "supported_commands":[
      "capture.begin","capture.end","capture.progress","class_poll",
      "connect","direct_operate","disable_unsolicited",
      "disconnect","enable_unsolicited","get_status","hello",
      "integrity_poll","read","select_and_operate","shutdown","stats",
      "wait_event","wait_unsolicited"
    ],
    "capabilities":{
      "APP.FC.01.READ":{
        "status":"IMPLEMENTED_UNVERIFIED",
        "implementation_revision":"t06-t08-opendnp3-3.1.2",
        "verification_scope":"local_opendnp3_outstation"
      },
      "OBJ.G30.V5":{
        "status":"IMPLEMENTED_UNVERIFIED",
        "implementation_revision":"t06-t08-opendnp3-3.1.2",
        "verification_scope":"local_opendnp3_outstation"
      }
    },
    "limits":{
      "max_request_bytes":1048576,
      "max_json_depth":64,
      "max_request_id_bytes":128,
      "recent_request_id_capacity":4096
    }
  }
}
```

`hello.capabilities` 只列当前后端实际接入的子集，值是带状态和验证范围的对象，不是字符串。G34、SAv5 等尚未接入能力的权威状态只在 `config/capability_matrix.csv` 中，不得为了展示“完整列表”伪造到 hello 响应。

---

## 附录 B：能力矩阵示例行

```csv
capability_id,edition,layer,feature,direction,subset_level,function_code,object_group,variations,qualifiers,std_reference,dut_pics_status,backend_status,framework_status,test_case_ids,evidence,owner,notes
APP.FC.01.READ,IEEE1815-2012,APPLICATION,Read,M2O,REVIEW_REQUIRED,1,,,,"IEEE 1815-2012 4.4.2; Table 12-32",UNKNOWN,IMPLEMENTED_UNVERIFIED,IMPLEMENTED_UNVERIFIED,"TC_APP_FC01_INTEGRITY_LOCAL_001|TC_APP_FC01_MULTI_HEADER_LOCAL_001|TC_APP_FC01_PYTHON_E2E_LOCAL_001","native/tests/opendnp3_read_integration_tests.cpp|python/tests/test_host_read.py",PROTOCOL_OWNER,"Local evidence only; independent interoperability remains pending"
OBJ.G34.V1,IEEE1815-2012,APPLICATION,"Analog Input Reporting Deadband variation 1",M2O,REVIEW_REQUIRED,,34,1,,REVIEW_REQUIRED,UNKNOWN,NOT_ANALYZED,BLOCKED,,,PROTOCOL_OWNER,"Exact clause and behavior require formal-standard review"
SEC.G120.V1.CHALLENGE,IEEE1815-2012,SECURITY,"G120V1 Challenge",BIDIRECTIONAL,REVIEW_REQUIRED,32|33|131,120,1,5B,REVIEW_REQUIRED,UNKNOWN,NOT_ANALYZED,BLOCKED,,,SECURITY_OWNER,"Requires approved security design formal vectors and independent implementation"
```

这些行只演示当前字段和有效 ID；权威内容始终是 `config/capability_matrix.csv`。`REVIEW_REQUIRED` 只能在标准负责人复核后替换。若状态升级为 `VERIFIED_*`，证据必须改成不可变文件引用，例如 `evidence/runs/<id>/manifest.json#sha256=<64 hex>`；公共矩阵的 `dut_pics_status` 仍保持 `UNKNOWN`。

---

## 附录 C：项目负责人每阶段检查单

- [ ] 本阶段输入和标准引用齐全。
- [ ] Agent 只执行了一个任务卡。
- [ ] 固定版本源码/API 已被实际阅读。
- [ ] 构建和测试命令有真实输出。
- [ ] 正向、边界、负向测试均存在。
- [ ] stdout 协议纯净，stderr 日志可控。
- [ ] IIN、quality、time、status 未被布尔化或丢弃。
- [ ] 超时、取消、断连和销毁路径已测。
- [ ] 队列/内存/文件/响应大小有上限。
- [ ] 改变 DUT 状态的操作经过安全门。
- [ ] 能力矩阵状态与证据相符。
- [ ] 独立实现证据没有被同栈自测替代。
- [ ] OpenDNP3 缺口明确显示，没有假完成。
- [ ] 下一任务仍然只有一个明确目标。

---

## 附录 D：首版风险登记

| 风险 | 影响 | 缓解措施 | 关闭证据 |
|---|---|---|---|
| OpenDNP3 上游归档 | 长期缺陷/安全维护落到内部 | 固定基线、最小 fork、双维护人、依赖扫描 | 维护计划和演练记录 |
| “全协议”范围被误解 | 虚假完成和漏测 | 原子能力矩阵、Profile/PICS/操作约定映射、分层覆盖率 | 矩阵审核 |
| 同栈互测掩盖缺陷 | 编解码双方犯相同错误 | 独立实现、黄金向量、正式一致性程序 | PCAP 和外部报告 |
| 大数据经 JSON 逐点传输 | 测试工具先成瓶颈 | C++ summary 聚合、batch、队列水位 | 工具上限报告 |
| 回调阻塞/生命周期错误 | 丢点、死锁、崩溃 | 轻回调、明确销毁序、压力和 sanitizer | 稳定性报告 |
| 控制/重启误操作 | EMS 状态改变或业务影响 | LAB 门、安全 token、风险标记、默认拒绝 | 运行授权记录 |
| Raw/Fuzz 误发生产 | 设备中断 | 网络隔离、目标 allowlist、构建开关 | 实验室审计 |
| SAv5 自研密码或泄密 | 严重安全缺陷 | 批准密码库、密钥存储、安全评审 | 安全签字和测试向量 |
| 标准版本混用 | 行为和枚举不一致 | edition 固定、条款引用、新版独立矩阵 | CI 版本检查 |
| 证据不可复现 | 无法判定回归/合规 | manifest、SHA-256、固定构建和配置 | 干净主机复现 |

---

## 附录 E：最终原则

1. 先把常用路径做薄、做稳、做可测，再按能力矩阵补齐高级功能。
2. Python API 保持稳定，协议栈和扩展封装在可替换 C++ 后端中。
3. 大数据量留在 C++ 内聚合；Python 做编排、断言和报告。
4. IEEE 1815-2012 的“全功能”以逐项标准目录和证据为准，不以代码行数或接口数量为准。
5. OpenDNP3 是首个后端，不是完整标准本身；其缺口必须显式存在，直到被真实实现和验证。
6. EMS 只需满足其 Device Profile、独立 PICS、批准操作约定和标准规定的适用项；框架则要能追踪全部标准能力。
7. 没有正式标准依据、独立互操作或安全前置条件时，正确动作是 `BLOCKED`，不是猜测。
