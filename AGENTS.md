# AGENTS.md

## 适用范围

本文件适用于仓库根目录及全部子目录。除非更深层目录另有 `AGENTS.md`，所有代理都应遵守本文件。

本项目是面向 Windows x64、IEEE 1815-2012 和 pytest 的 DNP3 主站自动化测试框架。生产目标使用 C++17/MSVC 构建，普通测试通过 Python 包调用独立的 `dnp3-master-host.exe`：

```text
pytest -> dnp3_master -> stdin/stdout NDJSON v1
       -> dnp3-master-host.exe -> OpenDNP3 3.1.2 -> EMS Outstation
```

## 开始工作前

1. 先阅读与任务直接相关的文件，不要只根据文件名猜测行为。
2. 架构或跨层改动先读 `docs/architecture.md`；协议改动先读 `docs/protocol.md`。
3. 控制、遥控、遥调、事故锁或真实 DUT 相关改动必须先读 `docs/SAFETY_INCIDENT_RUNBOOK.md`。
4. 先检查 `git status --short`，保留用户已有改动；不要重置、覆盖或清理无关内容。
5. 默认只做本机、只读、离线验证。不要自行连接真实 EMS/DUT，也不要自行解锁状态改变测试。

## 目录职责

- `native/include/`：C++ 公共接口。
- `native/src/`：host、NDJSON 控制器和 OpenDNP3 后端实现。
- `native/tests/`：C++ 单元、协议、运行时和本机集成测试。
- `python/src/dnp3_master/`：pytest 面向的公开 Python API、进程管理、模型、安全门和证据逻辑。
- `python/tests/`：Python 单元与集成测试。
- `schemas/`：NDJSON、EMS Profile、证据和事故记录的严格 Schema。
- `config/capability_matrix.csv`：能力实现和验证状态的唯一台账。
- `config/*.example.*`：可提交的脱敏配置模板。
- `scripts/`：环境检查、构建、测试、自检、打包和校验入口。
- `docs/`：架构、协议、操作、安全和标准差距说明。
- `third_party/`：固定版本的离线依赖；除非任务明确要求升级依赖，否则不要修改。
- `out/`、`.venv/`、缓存和 `*.egg-info/`：生成内容，不要手工编辑或提交。

## 支持环境

- Windows x64。
- Windows PowerShell 5.1 或 PowerShell 7。
- Visual Studio 2022 Build Tools，MSVC v143 x64 和 Windows SDK。
- CMake 3.25 或更高版本。
- Python 3.10 或更高版本。
- pytest 8.x 或 9.x。

项目固定使用 OpenDNP3 3.1.2 及锁定的 vendored 依赖。正常配置和构建必须保持离线；不要增加隐式下载、浮动版本或运行时网络取依赖。

## 常用命令

在仓库根目录使用 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\python[test]"
.\scripts\doctor.ps1
```

构建、完整测试和本机回环：

```powershell
.\scripts\build.ps1 -Preset windows-msvc-release
.\scripts\test.ps1 -Preset windows-msvc-release
.\scripts\run-local-self-test.ps1 -Preset windows-msvc-release
.\scripts\test-compatibility.ps1 -PackageRoot .\out\package\ems-dnp3-pytest-0.6.1
```

其他受支持的 preset 是 `windows-msvc-debug` 和 `windows-msvc-asan`。测试脚本依赖已构建的 host 和本机测试从站。

针对性验证可使用：

```powershell
Push-Location .\python
& ..\.venv\Scripts\python.exe -m pytest tests\test_client.py -q
Pop-Location

.\.venv\Scripts\python.exe -m pytest .\scripts\tests -q
ctest --preset windows-msvc-release --output-on-failure
.\.venv\Scripts\python.exe .\scripts\validate_capabilities.py .\config\capability_matrix.csv
.\.venv\Scripts\python.exe .\scripts\validate_dependencies.py .\third_party\opendnp3-dependencies.lock.json
```

只有发布或打包相关任务才运行：

```powershell
.\scripts\release.ps1 -LifecycleIterations 1000
```

正式发布入口要求工作区已提交且 clean。`package.ps1 -AllowNonCleanBuild` 只用于
检查未提交代码的包结构，不得把其产物发布或作为正式证据。

## 实现约定

### 跨层边界

- pytest 和业务用例只依赖 `dnp3_master` 的公开 API，不直接暴露 OpenDNP3/C++ 类型。
- Python client 与 host 保持单会话、单在途请求模型；不要在没有独立设计和迁移任务时扩大并发边界。
- stdout 只能输出一行一个 UTF-8 JSON 协议消息；日志和诊断必须写 stderr。
- 调用方按稳定的 `error.code` 和结构化 `details` 分支，不匹配人类可读的错误文本。
- 所有等待、队列、输入大小和结果集合都必须有明确上限；溢出或不完整结果必须失败，不能静默截断或假成功。

### C++

- 使用 C++17，并保持 MSVC `/W4`、`/permissive-` 下无新增警告。
- OpenDNP3 回调应快速写入有界状态并返回，不等待 Python、stdout 或长时间操作。
- 资源关闭顺序和任务取消顺序是安全不变量；修改连接生命周期时必须补充失败、超时和重复关闭测试。
- 新实现放入项目自己的 `native/` 层，不直接修改 vendored OpenDNP3 来绕过接口问题。

### Python

- 支持 Python 3.10+；核心包当前只依赖标准库。新增运行时依赖必须有明确必要性，并同步更新打包和离线约束。
- 公共输入使用严格、类型化、可验证的模型；拒绝未知、重复、越界、NaN/Infinity 或含糊输入。
- 保持异常类型、结构化上下文、超时行为和进程回收语义稳定。
- 测试放在 `python/tests/`，优先使用现有 fake host、本机 outstation 和 fixtures，避免真实网络依赖。

### PowerShell

- 脚本必须同时兼容 Windows PowerShell 5.1 和 PowerShell 7。
- 使用 `Set-StrictMode -Version Latest` 和 `$ErrorActionPreference = 'Stop'`，并检查外部进程退出码。
- PowerShell 5.1 不支持行首管道；跨行管道必须把 `|` 放在上一行末尾。
- 捕获外部命令诊断时同时处理 stdout、stderr 和空输出，失败信息不能再次触发参数绑定错误。
- 修改公共脚本后至少用 Windows PowerShell 5.1 解析；适用时实际运行对应入口。

### Schema、能力和文档

- 修改协议字段或新增命令时，同步检查 C++ host、Python client/models、`schemas/`、`docs/protocol.md` 和相应测试。
- 修改能力时同步更新 `config/capability_matrix.csv` 并运行能力校验；没有独立互操作或一致性证据，不得把状态提升为已验证。
- 用户可见行为、环境变量、命令或安全流程变化时同步更新 README 和相关 `docs/`。
- 版本发布改动需核对根 `CMakeLists.txt`、`python/pyproject.toml`、文档、包清单和构建信息是否一致。

## 离线依赖完整性

- `third_party/opendnp3-dependencies.lock.json` 固定归档和源码树哈希；校验失败时先找出缺失、多余或内容变化的文件。
- 不得通过更新锁哈希来接受未经审查的源码差异。应从锁定且归档哈希已验证的 `third_party/distfiles/` 恢复原文件。
- vendored 源码自带的嵌套 `.gitignore` 可能误排除合法源文件；恢复被忽略但属于锁定源码树的文件时，应核对归档后使用精确的 `git add -f -- <paths>`。
- 不要改写第三方源码来修复项目层接口问题，也不要引入网络回退。

## 安全硬约束

用户已确认当前 EMS 的设备为模拟器且允许不限下发。使用项目已实现的显式
SIMULATOR 模式（见 `docs/SIMULATOR_MODE.md`），不要重新把 LAB 审批、身份、持久
事故锁、单场景选择或强制恢复套到模拟器。仍不自行连接未被置于任务范围内的网络。

- 未启用 SIMULATOR 时控制默认锁定。LAB 模式不得为了让测试通过而绕过 pytest 收集门、会话 safety token、operator ID、DUT ID 或持久事故锁。
- 未获得用户明确授权时，不运行带 `dnp3_state_changing` 的测试，不设置状态改变开关，也不向真实 DUT 发送 CROB/Analog Output 命令。
- 控制超时或结果不确定时不隐藏重试，必须销毁会话。LAB 保留事故锁并独立读回/显式确认；SIMULATOR 不访问事故锁，新 client 或下一条 pytest 用例可继续，无需人工解锁。
- 不直接删除、改名或编辑 `active/*.json` 事故锁。
- LAB 同一 DUT 的控制测试必须串行，不使用 pytest-xdist。SIMULATOR 允许批量/重复；并行写同一点会干扰反馈，测试应自行隔离状态。
- 未实现能力必须返回稳定且明确的错误，例如 `UNSUPPORTED_BY_BACKEND`；禁止空实现、伪造测量或返回假成功。

## 敏感数据与提交边界

不要读取、输出、打包或提交不属于任务所需的本地敏感内容，尤其是：

- `config/ems.local.json`、`config/points.local.csv` 和其他 `*.local.*`。
- `secrets/`、`evidence/local/`、PCAP、密钥和证书。
- 本地 IEEE 标准 PDF、真实 PICS、真实点表、DUT 地址、operator ID 和 safety token。

示例、测试夹具和日志必须使用文档保留地址或明显虚构值。正式证据只记录必要的脱敏信息和哈希；外发前仍需人工审查。

## Git 工作约定

- 未经用户明确要求，不创建提交、不推送、不改写历史。
- 提交前检查状态和 diff，只暂存当前任务文件；不要夹带生成物或无关改动。
- 推送前确认远端 URL、目标分支、待推送提交和工作区状态；不得猜测远端归属。
- 不使用 `git reset --hard`、`git checkout --` 或其他会丢弃用户改动的命令，除非用户明确要求精确操作。

## 验证要求

采用与改动风险相称的最小验证集，并在交付时说明实际运行过的命令和未运行项：

- Python 局部逻辑：运行对应 `python/tests/` 用例。
- C++/协议逻辑：构建对应 preset，并运行相关 CTest；跨层行为再运行对应 Python host 测试。
- Schema、能力或依赖锁：运行相应 validator 及 `scripts/tests/`。
- 生命周期、进程回收或网络状态：至少运行完整 `scripts/test.ps1`；适用时增加本机回环或 lifecycle 测试。
- 发布、清单或可移植性：运行打包流程及解包回环自检。
- 文档或纯注释改动：检查链接、路径、命令和术语即可，不必无意义地全量构建。

## 完成标准

任务完成时应满足：

1. 改动范围小且符合现有分层，没有顺手重构无关代码。
2. 安全门、离线构建、固定依赖和有界资源约束保持不变或更严格。
3. 行为改动有对应测试，协议、Schema、能力和文档保持同步。
4. 未引入真实设备数据、密钥、证据、构建产物或缓存。
5. 最终说明改了什么、验证了什么，以及仍存在的风险或环境限制。
