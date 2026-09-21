# EMS_DNP3 拉取、构建、移植与使用指南（Windows/pytest 小白版）

当前“EMS 监听、pytest 主动连接模拟 EMS”的最短路径见
[单配置入门指南](SIMULATOR_QUICKSTART.md)。完成本文构建/运行包准备后，只复制
`examples/pytest_simulator` 并填 `settings.local.json`，无需 LAB 工单或信号生成接口开发。

如果 EMS 连接的全部是模拟设备，请先看 [模拟器入口](SIMULATOR_MODE.md)。构建和
复制方法不变；pytest 配置一次 `dnp3_simulator = true`，本文中 LAB 的 operator/DUT ID、
工单、事故解锁和逐次场景选择均不需要。使用精简的模拟器场景模板，不必照搬 LAB 的
强制初值与恢复要求；本次需同时更新 Python、host 和场景文件。

本文面向不熟悉 C++ 的测试开发人员。正常使用时，你只需要写 Python/pytest；C++ 已封装在 `dnp3-master-host.exe` 中，不需要在测试代码里调用 OpenDNP3，也不需要理解 C++ 指针或编译器细节。

> 当前版本：0.6.1，目标平台 Windows x64，固定协议栈 OpenDNP3 3.1.2。当前实现已完成本机 TCP、Read/Class Poll、主动上报、测量值/IIN、CROB 和四种 Analog Output 控制的同栈回归，并提供持续 capture、大点表/事件性能和可中断 soak 工具；这些本机结果仍不代表真实 EMS 互操作、正式性能结论或 IEEE 一致性认证。

## 1. 先理解四个目录

| 目录/文件 | 用途 | 普通 pytest 开发是否要改 |
|---|---|---|
| `python/src/dnp3_master/` | 给 pytest 使用的 Python 包、fixture、数据模型 | 通常只调用，不修改 |
| `bin/dnp3-master-host.exe` | Python 与 DNP3 网络之间的 C++ 宿主进程 | 不修改 |
| `config/` | 能力矩阵及 EMS PICS、点表、场景计划、性能/事件负载示例 | 复制示例并填写本地值 |
| `schemas/` | JSON 协议和 EMS 配置格式校验 | 不修改 |

> 路径约定：源码仓库构建出的 EXE 位于 `out\build\windows-msvc-release\bin\`；上表中的 `bin\` 是第 5 章生成的可移植包目录。不要把整个 `out\build\windows-msvc-release` 当作可移植包复制。

工作链路是：

```text
pytest 用例 -> dnp3_master Python 包 -> dnp3-master-host.exe -> TCP/DNP3 -> EMS
```

因此，日常移植的核心是“复制 Python 包和已经编译好的 EXE”，不是把 C++ 源码嵌进原框架。

## 2. 两种使用路线

先区分两个位置：

- **源码仓库**：本项目克隆后的 `EMS_DNP3` 目录，用于构建和维护 C++/Python 代码。
- **目标 pytest 项目**：你原有的自动化测试框架，最终从这里运行业务用例。

| 你的目标 | 选择 | 后续章节 |
|---|---|---|
| 直接在本源码仓库编写并运行 pytest | 路线 A | 完成第 3～4 章，跳过第 5 章，直接进入第 6 章 |
| 在内网从源码构建，再接入另一个 pytest 框架 | 路线 A | 完成第 3～4 章，再按第 5 章生成并复制可移植包 |
| 构建机与内网 pytest 机器分离 | 路线 B | 构建机在本章生成包；目标机器从第 5 章的复制与接入步骤继续 |

### 路线 A：在内网从源码构建

适合内网允许安装 Visual Studio Build Tools，并且后续需要修改 C++ 的情况。先按第 3～4 章完成环境准备、构建、测试和本机自检。

- 如果当前源码仓库本身就是你的 pytest 项目，不需要复制任何目录，也不需要执行第 5 章；第 6 章使用 `out\build\windows-msvc-release\bin\dnp3-master-host.exe`。
- 如果还要把能力接入另一个既有 pytest 框架，继续执行第 5 章。第 5 章会把已经通过测试的 Release 构建整理成正确的可移植目录。

### 路线 B：在外网构建可移植包，再拷入内网

适合内网机器只运行 pytest、不修改 C++ 的情况。先在有构建工具的 Windows x64 机器执行：

```powershell
cd D:\Work\Code\EMS_DNP3
.\scripts\release.ps1 -LifecycleIterations 1000
```

产物位于：

```text
out\package\ems-dnp3-pytest-0.6.1\
out\package\ems-dnp3-pytest-0.6.1.zip
out\package\ems-dnp3-pytest-0.6.1.zip.sha256
```

发布过程要求代码已经提交且工作区干净，会执行 Release 全量回归、1,000 次生命周期、
两次确定性打包、解包回环和空白 pytest 消费者迁移验收。将 ZIP 和 `.sha256` 一起
传入内网；传输后先用 `Get-FileHash -Algorithm SHA256` 与旁车文件第一列比对，再
解压。包中包含主程序、只用于本机自检的测试从站、Python 源码和离线 wheel、
Schema、能力矩阵、严格点表/场景/性能示例、可复制 EMS 与只读性能 pytest 套件、
迁移验收入口、依赖锁、许可证和本文档，不包含 IEEE 标准 PDF、EMS 本地配置、抓包
或密钥。

内网目标机器若不安装 Build Tools，通常仍需安装 Microsoft Visual C++ 2015–2022 Redistributable x64 和 Python 3.10 或更高版本。

路线 B 在目标 pytest 机器上不需要重复第 3～4 章的 C++ 构建；把 ZIP 校验并解压后，直接按第 5 章完成 Python 接入和包内自检。

## 3. 源码构建前的准备工作

### 3.1 必需软件

- Windows 10/11 x64 或 Windows Server x64。
- Git。
- Visual Studio 2022 Build Tools，至少包含“使用 C++ 的桌面开发”、MSVC v143 x64 和 Windows SDK。
- CMake 3.25 或更高版本。Visual Studio 自带版本也可以。
- Python 3.10 或更高版本。
- pytest 8.x 或 9.x（只在执行 Python 测试时需要）。

在普通 PowerShell 中检查：

```powershell
git --version
cmake --version
python --version
```

普通 PowerShell 中找不到 `cl.exe` 不一定有问题；项目脚本会自动定位 Visual Studio 的开发环境。若脚本仍提示找不到 MSVC，再检查 Visual Studio Installer 中的 C++ 工作负载和 Windows SDK。

### 3.2 获取源码

有 GitHub SSH Key 时：

```powershell
git clone git@github.com:fau-linrui/EMS_DNP3.git
cd EMS_DNP3
```

没有 SSH Key 时可尝试 HTTPS：

```powershell
git clone https://github.com/fau-linrui/EMS_DNP3.git
cd EMS_DNP3
```

内网不能访问 GitHub 时，在可访问 GitHub 的机器下载/克隆后，通过批准的介质或内部 Git 镜像传入。不要单独漏拷 `third_party/`；C++ 的离线构建依赖已经固定在仓库中。

### 3.3 建立 Python 虚拟环境

以下命令不要求“激活”虚拟环境，直接调用其中的 Python，最不容易出错：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\python[test]"
```

完全离线时，核心 `dnp3_master` 包没有第三方运行时依赖，但 pytest/setuptools 仍需由内网软件源或本地 wheel 提供。如果内网已有 pytest，也可以暂不执行 pip 安装，运行脚本前把 `python\src` 加到 `PYTHONPATH`。

安装后先执行环境体检。它会检查 VS/CMake/Python/pytest、全部离线源码摘要和能力矩阵，不连接互联网或 EMS：

```powershell
.\scripts\doctor.ps1
```

只有最后显示 `READY` 才继续构建。需要机器可读结果时使用 `.\scripts\doctor.ps1 -Json`。

固定依赖检查包含 OpenDNP3 本体、nlohmann/json 和三项构建依赖，不只是检查文件
是否存在。正常 Windows CRLF/LF 换行差异可接受；源码缺失、多余或实际内容变化
会失败。请从已验证的离线归档恢复准确文件，不要为了通过检查修改锁文件里的哈希。

## 4. 一键构建、测试和本机自检

在仓库根目录执行：

```powershell
.\scripts\doctor.ps1
.\scripts\build.ps1 -Preset windows-msvc-release
.\scripts\test.ps1 -Preset windows-msvc-release
.\scripts\run-local-self-test.ps1 -Preset windows-msvc-release
```

成功后主要文件位于：

```text
out\build\windows-msvc-release\bin\dnp3-master-host.exe
out\build\windows-msvc-release\bin\build-info.json
out\build\windows-msvc-release\bin\dnp3-local-test-outstation.exe
```

本机自检会临时启动仓库自带的 OpenDNP3 从站，在回环地址上完成一次连接、读取和受控命令，然后关闭全部进程。它证明“安装、EXE、Python 包和基本调用链可工作”，但因为主站和从站都使用 OpenDNP3 3.1.2，不能作为独立互操作、真实 EMS 验证或 IEEE 一致性证据。

开发调试可使用 Debug：

```powershell
.\scripts\build.ps1 -Preset windows-msvc-debug
.\scripts\test.ps1 -Preset windows-msvc-debug
```

长期运行/资源回收验收：

```powershell
.\scripts\test-lifecycle.ps1 -Preset windows-msvc-release -Iterations 1000
```

脚本会先预热一次，再比较当前 pytest 进程的句柄和线程数。通过条件是线程数不增长，句柄最终值不超过预热基线加 8（给 Windows/pytest 的小幅系统噪声留余量），并且每个 host 都以返回码 0 退出且无需强制清理。看到类似 `handles=148->150` 并不自动等于泄漏，应以 pytest 最终是否通过为准；若失败，不要提高阈值掩盖问题，应保留完整输出并定位未关闭的进程、管道或 Job Object。

### 4.1 生成正式 clean 发布包

前面的分步命令适合开发排错。要生成可交付制品，先提交本次代码并确认
`git status --short` 没有输出，再执行唯一正式入口：

```powershell
.\scripts\release.ps1 -LifecycleIterations 1000
```

它会重新完成环境、Release 构建、全量测试、生命周期、两次确定性打包和迁移验收，
并把机器可读报告写到 `out\release`。如果源码是 dirty、构建属于旧 commit、不是
Release/x64，或者两次 ZIP 哈希不同，脚本都会停止。它不创建 commit/tag，也不推送。

`package.ps1 -AllowNonCleanBuild` 只用于开发者查看未提交代码的包结构；该产物不得
传入内网、不得作为正式测试证据。完整规则见
`docs/RELEASE_AND_MIGRATION_ACCEPTANCE.md`。

## 5. 接入另一个 pytest 自动化框架

本章只适用于“把 DNP3 能力接入另一个 pytest 项目”。如果你准备直接在当前源码仓库中编写和运行用例，请跳过本章并进入第 6 章。

### 5.1 先取得可移植包

**路线 A：你已按第 4.1 章完成正式发布闭环。** 直接使用该入口生成的目录/ZIP：

```text
out\package\ems-dnp3-pytest-0.6.1\
out\package\ems-dnp3-pytest-0.6.1.zip
out\package\ems-dnp3-pytest-0.6.1.zip.sha256
out\release\release-closure-report.json
```

不要在发布后修改源码再手工执行 `package.ps1 -SkipBuild`。打包门禁会拒绝“旧 EXE +
新 Python”的陈旧组合。

**路线 B：你已经在第 2 章的外部构建机生成并传输了 ZIP。** 在目标机器核对 `.zip.sha256` 后，把 ZIP 解压到目标 pytest 项目的 `third_party\ems_dnp3\`，不需要再次执行 `package.ps1`。

两条路线最终都应得到以下可移植内容：

```text
ems-dnp3-pytest-0.6.1\
  CHANGELOG.md
  bin\dnp3-master-host.exe
  python\
  python-dist\dnp3_master_test_framework-0.6.1-py3-none-any.whl
  config\
  schemas\
  tools\dnp3-local-test-outstation.exe
  self-test.ps1
  compatibility-test.ps1
  migration-consumer\
  package-manifest.json
  ...
```

不要复制整个 `out\build\windows-msvc-release`。它包含 CMake 缓存、中间文件和开发测试程序，不是受支持的移植边界。

### 5.2 把整个包放进目标项目

假设原有 pytest 项目位于 `D:\Automation\MyPytest`，推荐把整个可移植目录复制并命名为 `third_party\ems_dnp3`：

```powershell
# 当前目录仍是 EMS_DNP3 源码仓库；目标 ems_dnp3 目录应尚不存在。
$targetProject = 'D:\Automation\MyPytest'
$packageRoot = Join-Path $targetProject 'third_party\ems_dnp3'
if (Test-Path -LiteralPath $packageRoot) {
  throw "目标目录已存在，请先人工确认旧包如何归档或替换：$packageRoot"
}
New-Item -ItemType Directory `
  -Path (Join-Path $targetProject 'third_party') `
  -Force | Out-Null
Copy-Item `
  -LiteralPath '.\out\package\ems-dnp3-pytest-0.6.1' `
  -Destination $packageRoot `
  -Recurse
```

路线 B 若传输的是 ZIP，则在校验 SHA-256 后直接解压：

```powershell
$targetProject = 'D:\Automation\MyPytest'
$packageRoot = Join-Path $targetProject 'third_party\ems_dnp3'
$zip = (Resolve-Path '.\ems-dnp3-pytest-0.6.1.zip').Path
$expectedHash = (
  (Get-Content -LiteralPath "$zip.sha256" -Raw).Trim() -split '\s+'
)[0].ToLowerInvariant()
$actualHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) {
  throw '可移植 ZIP 的 SHA-256 与旁车文件不一致，停止解压。'
}
if (Test-Path -LiteralPath $packageRoot) {
  throw "目标目录已存在，请先人工确认旧包如何归档或替换：$packageRoot"
}
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
Expand-Archive `
  -LiteralPath $zip `
  -DestinationPath $packageRoot
```

完成后的目标项目结构应为：

```text
你的自动化项目\
  conftest.py
  tests\
  third_party\ems_dnp3\
    CHANGELOG.md
    bin\dnp3-master-host.exe
    python\
    python-dist\dnp3_master_test_framework-0.6.1-py3-none-any.whl
    config\
    schemas\
    tools\dnp3-local-test-outstation.exe
    self-test.ps1
    compatibility-test.ps1
    migration-consumer\
    examples\pytest_ems\
    examples\pytest_performance\
    package-manifest.json
    ...
```

### 5.3 先做空白 pytest 迁移验收

切换到目标 pytest 项目根目录。先让包内脚本使用目标框架自己的 Python/pytest 做
隔离验收，并把报告放在包目录之外：

```powershell
cd D:\Automation\MyPytest
$packageRoot = (Resolve-Path '.\third_party\ems_dnp3').Path
& (Join-Path $packageRoot 'compatibility-test.ps1') `
  -PythonExecutable '.\.venv\Scripts\python.exe' `
  -ReportPath '.\artifacts\dnp3-migration-compatibility.json'
```

它会校验整个包，使用包内 `127.0.0.1` 测试从站完成读/控/capture 回环，再把 wheel
临时安装到隔离目录并运行一个空白 pytest 项目。它会清空继承的 `DNP3_*` 配置，
不会连接 EMS，也不会改动目标虚拟环境或发布包。只有
`overall_passed=true` 才继续。

### 5.4 安装 wheel 并启用 pytest 插件

使用包内 wheel 安装 Python 层。命令显式禁止网络和依赖解析：

```powershell
$wheel = @(Get-ChildItem `
  -LiteralPath (Join-Path $packageRoot 'python-dist') `
  -Filter '*.whl' `
  -File)
if ($wheel.Count -ne 1) {
  throw "期望一个 Python wheel，实际为 $($wheel.Count) 个。"
}
.\.venv\Scripts\python.exe -m pip install `
  --no-index `
  --no-deps `
  $wheel[0].FullName
```

不要对 `third_party\ems_dnp3\python` 执行 `pip install -e` 或现场 source build；pip
可能写入 `build\`/`*.egg-info`，导致 `package-manifest.json` 失效。若内部策略禁止
安装 wheel，可以在运行 pytest 前只设置源码路径（不会改写包）：

```powershell
$env:PYTHONPATH = "$PWD\third_party\ems_dnp3\python\src"
```

在既有框架根目录的 `conftest.py` 增加：

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

如果已有 `pytest_plugins`，把字符串追加到原元组中，不要再定义第二个同名变量。

### 5.5 指定 Host 并确认插件

仍在目标 pytest 项目根目录执行：

```powershell
$env:DNP3_MASTER_HOST_EXE = (
  Resolve-Path (Join-Path $packageRoot 'bin\dnp3-master-host.exe')
).Path
```

第 5.3 章的 `compatibility-test.ps1` 已调用 `self-test.ps1`，它只启动包内本机测试
从站，不连接真实 EMS。除读回和控制反馈循环外，它还会精确采集 BI/AI/BOS/AOS
索引 0～1 共 8 点。成功输出中应看到 `"capture_expected": 8`、
`"capture_received_unique": 8` 和 `"capture_valid": true`；缺少这些字段通常表示
混用了旧 Python 包或旧 EXE。再确认目标项目已加载 DNP3 参数：

```powershell
.\.venv\Scripts\python.exe -m pytest --help |
  Select-String 'dnp3-host-exe'
```

到这里，第 5 章才算完成。接下来进入第 6 章，准备私有 PICS、点表和只读连接参数。

### 5.6 不要人工裁剪可移植包

对普通移植，最小受支持的复制单位就是整个
`ems-dnp3-pytest-0.6.1\` 目录或原始 ZIP。`package-manifest.json` 覆盖包内每个
文件；手工删除 `tools`、wheel、迁移 consumer、Schema、许可证、文档或示例后，逐文件校验必然失效，
也不能再把该目录称为经过验收的完整可移植包。

其中 `tools\dnp3-local-test-outstation.exe` 只在本机自检时启动，不会参与真实
EMS 连接，但应与 `self-test.ps1` 一起保留，便于区分“框架坏了”还是“EMS/网络
配置有问题”。如果内部制品规则确实要求更小的发布物，应另建一张受评审的重新
打包任务，明确运行边界、重新生成清单和 SHA-256，并重新执行解包自检；不要在
复制完成后临时删文件。

## 6. 第一次连接真实 EMS（只读）

### 6.1 先准备 PICS 和连接参数

从 EMS 负责人取得以下信息，不能猜：

- EMS 厂商、型号、固件版本和 Device Profile/PICS 版本。
- EMS 是否作为 DNP3 Outstation/TCP Server 监听。
- IP、TCP 端口、本地主站链路地址、EMS 从站链路地址。
- 支持的对象组/变体、Class 分配、最大响应/分片、主动上送策略。
- 点表：类型、索引、Class、量程、是否可写。

仓库中的 `docs/standards/ems_device_profile.md` 已记录目前取得的“DNP3 操作约定”。它只是部分输入，不是正式 PICS：其中事件 Read、FC6 响应、SBO、CROB 点模型、广播和“遥脉同遥测”仍有冲突或歧义。先取得该文档 D01～D09 的厂商书面答复；未关闭的能力必须保留 `UNKNOWN`，不要通过连接真实设备试错来猜遥控含义。

如果在**源码仓库**中直接运行，复制 PICS 示例到被 Git 忽略的本地文件：

```powershell
Copy-Item .\config\ems_profile.example.json .\config\ems.local.json
Copy-Item .\config\points.example.csv .\config\points.local.csv
Copy-Item .\config\ems_test_plan.example.json .\config\ems_test_plan.local.json
```

如果已经按第 5 章接入**另一个 pytest 项目**，在目标项目根目录执行：

```powershell
New-Item -ItemType Directory -Path '.\config' -Force | Out-Null
Copy-Item `
  '.\third_party\ems_dnp3\config\ems_profile.example.json' `
  '.\config\ems.local.json'
Copy-Item `
  '.\third_party\ems_dnp3\config\points.example.csv' `
  '.\config\points.local.csv'
Copy-Item `
  '.\third_party\ems_dnp3\config\ems_test_plan.example.json' `
  '.\config\ems_test_plan.local.json'
```

`points.local.csv` 必须保持示例中的精确列名。加载器会拒绝未知/缺失列、重复 point ID、重复“点类型+索引”、非法对象变体、非法 Class、非有限量程和错误布尔值。该表只描述可读对象，不能擅自增加控制列。

`ems_test_plan.local.json` 单独描述完整性/Class、主动上报和控制闭环。它会与点表交叉校验；主动上报和控制示例默认关闭。真实控制的危险等级、批准工单、操作/恢复值和反馈关系只放在内部受控的本地计划，不得提交到公开仓库。

只根据正式 Device Profile/PICS 或经批准的项目决定，把每项填写为：

- `SUPPORTED`：EMS 声明支持，正向用例可以运行。
- `NOT_SUPPORTED`：EMS 声明不支持，正向用例跳过，可运行专门的不支持行为用例。
- `UNKNOWN`：证据不足；默认 `xfail(run=False)`，不会触碰 EMS。

不要为了让测试“变绿”而把 `UNKNOWN` 改成 `SUPPORTED`。

插件会把 PICS 中的每个能力 ID 与 `config\capability_matrix.csv` 交叉检查，拼错或不存在的 ID 会在收集阶段直接报错。PICS 写成 `SUPPORTED` 也不能覆盖框架的 `BLOCKED` 或 `UNSUPPORTED_BY_BACKEND`；此类用例会在接触 EMS 前跳过。插件还会把矩阵 SHA-256 与 host 握手值比较，防止复制时混入旧 EXE。整包/推荐目录无需额外参数；如果你改变了目录布局，请同时传入 `--dnp3-capability-matrix ".\third_party\ems_dnp3\config\capability_matrix.csv"`。

### 6.2 在任何 EMS 连接前执行离线预检

先不要填写 IP/端口，也不要启动 DUT 用例。在源码仓库中执行：

```powershell
.\.venv\Scripts\python.exe -m dnp3_master.preflight `
  --pics .\config\ems.local.json `
  --points .\config\points.local.csv `
  --plan .\config\ems_test_plan.local.json `
  --capability-matrix .\config\capability_matrix.csv
```

移植到另一个 pytest 项目后，把最后一个路径改为 `third_party\ems_dnp3\config\capability_matrix.csv`。命令只读取本地文件，不启动 host、不建立 TCP 连接：

- 退出码 `0`：离线配置 `READY`，可以继续准备只读 DUT 测试；
- 退出码 `2`：文件格式、字段、引用或边界无效，必须先修复；
- 退出码 `3`：格式有效，但仍有 `FILL_ME`、PICS `UNKNOWN/NOT_SUPPORTED`、缺失能力或框架未实现项。

仓库示例故意返回 3。必须依据正式资料修正私有输入，不能为了“变绿”修改报告或把未知能力猜成支持。需要保存机器可读证据时加 `--json`；详细规则见 `docs/OFFLINE_PREFLIGHT.md`。

### 6.3 写一个只读 pytest 用例

```python
import pytest


_REQUEST_ERROR_IIN_BITS = frozenset(
    {
        "IIN2.0.NO_FUNC_CODE_SUPPORT",
        "IIN2.1.OBJECT_UNKNOWN",
        "IIN2.2.PARAMETER_ERROR",
    }
)


@pytest.mark.dnp3_dut
@pytest.mark.dnp3_capability("APP.FC.01.READ")
@pytest.mark.dnp3_capability("APP.CLASS.EVENTS")
@pytest.mark.dnp3_capability("QUAL.Q06.REVIEW")
@pytest.mark.dnp3_capability("OBJ.G60.V1")
@pytest.mark.dnp3_capability("OBJ.G60.V2")
@pytest.mark.dnp3_capability("OBJ.G60.V3")
@pytest.mark.dnp3_capability("OBJ.G60.V4")
def test_ems_integrity_read(connected_master):
    result = connected_master.integrity_poll(
        timeout=10.0,
        max_measurements=100_000,
        return_mode="detail",
    )
    assert result.task_status == "SUCCESS"
    assert _REQUEST_ERROR_IIN_BITS.isdisjoint(result.iin["bits"])
    assert result.iin["observation_window_dropped"] == 0
    assert result.summary["received_total"] == len(result.measurements)
```

`raw_hex` 是必须存在的四位字符串，`"0000"` 也会让普通真值断言通过，因此不能用 `assert result.iin["raw_hex"]` 判断请求是否被 EMS 接受。手写用例还必须像上例一样声明全部实际依赖；插件不会解析函数体来推断 G60 或 Q06。推荐使用现成场景模板自动生成这些标记。

下面的命令以“已经按第 5 章接入另一个 pytest 项目”为例，运行时显式传入本地参数：

```powershell
.\.venv\Scripts\python.exe -m pytest .\tests\test_ems_read.py -v `
  --dnp3-host-exe ".\third_party\ems_dnp3\bin\dnp3-master-host.exe" `
  --dnp3-pics-file ".\config\ems.local.json" `
  --dnp3-points-file ".\config\points.local.csv" `
  --dnp3-ems-plan ".\config\ems_test_plan.local.json" `
  --dnp3-evidence-dir ".\evidence\local" `
  --dnp3-unknown-policy error `
  --dnp3-outstation-host "192.0.2.10" `
  --dnp3-outstation-port 20000 `
  --dnp3-master-address 1 `
  --dnp3-outstation-address 1024
```

如果直接在源码仓库中运行路线 A，把 `--dnp3-host-exe` 改为：

```text
--dnp3-host-exe ".\out\build\windows-msvc-release\bin\dnp3-master-host.exe"
```

将示例 IP 和地址换成实验 EMS 的真实参数。`--dnp3-unknown-policy error` 适合正式执行，可防止因 PICS 漏填而悄悄跳过。

如不想从零写 LAB 用例，可把包内 `examples\pytest_ems` 整体复制进既有框架。未提供配置时示例会安全跳过；提供严格点表和场景计划后，可收集逐点 Static Read、完整性/Class、主动上报和控制闭环用例。LAB 示例的主动上报与控制默认关闭，控制还须逐次精确选择；当前模拟 EMS 改用单配置入门入口，不要求这种选择。通用计划模板会附加实际功能码、Group/Variation 和限定符能力 ID。固定后端不能表达 Q02/Q09/Q39，不能用 16-bit 请求冒充。

`--dnp3-evidence-dir` 会为每次运行创建独立目录，生成脱敏 `manifest.json` 和 `pytest-results.json`，只记录 PICS、点表、场景计划和矩阵的文件名、大小和 SHA-256，不复制私有原文。记录器会替换已知的项目、测试、host、输入和证据绝对路径，并遮盖常见密钥字段；但任意第三方库输出可能包含记录器不了解的业务数据，因此证据对外传递前仍必须人工复核。

每条结果的 `case_id` 在本次运行内唯一；`nodeid` 是供人阅读的脱敏名称，两个
参数化用例可能显示相同名称，但不会合并结果。自己编写报告汇总时使用
`run_id + case_id` 关联阶段，不要把脱敏名称当作唯一键。

范围读取示例：

```python
import pytest

from dnp3_master import ReadHeader


@pytest.mark.dnp3_dut
@pytest.mark.dnp3_capability("APP.FC.01.READ")
@pytest.mark.dnp3_capability("OBJ.G30.V5")
@pytest.mark.dnp3_capability("QUAL.Q01.REVIEW")
def test_one_analog_point(connected_master):
    result = connected_master.read(
        [ReadHeader.range16(group=30, variation=5, start=0, stop=0)],
        timeout=5.0,
    )
    assert not {
        "IIN2.0.NO_FUNC_CODE_SUPPORT",
        "IIN2.1.OBJECT_UNKNOWN",
        "IIN2.2.PARAMETER_ERROR",
    }.intersection(result.iin["bits"])
    assert result.iin["observation_window_dropped"] == 0
    analogs = result.measurements_of_kind("analog_input")
    assert len(analogs) == 1
    assert analogs[0].index == 0
```

该例按当前 EMS 操作约定使用 G30V5 和 16-bit range/Q01。若真实 PICS/点表选择其他 Variation 或索引宽度，必须同时修改 Header、对象能力 ID 和 Qualifier 能力 ID，不能只改其中一处。

还可调用 `class_poll((1, 2, 3))`，或一次向 `read([...])` 传入最多 64 个严格校验的 Header。大量点优先使用 `return_mode="summary"`，避免在结果中保留和传输巨大的逐点 JSON。当前 EMS 约定声称事件/Class 1～3 Read 恒为空，这与标准事件轮询存在差异；只有在“确认无事件”和“人工产生已知事件”两个场景都保存证据后，才能形成 DUT 结论。

主动上送必须显式启停，不会在连接时偷偷开启：

```python
enabled = connected_master.enable_unsolicited((1, 2), timeout=5.0)
try:
    assert enabled.task_status == "SUCCESS"
    batch = connected_master.wait_unsolicited(
        wait_timeout=10.0,
        max_events=256,
    )
    assert batch.summary["dropped_total"] == 0
    assert all(item.source == "unsolicited" for item in batch.measurements)
finally:
    disabled = connected_master.disable_unsolicited((1, 2), timeout=5.0)
    assert disabled.task_status == "SUCCESS"
```

队列默认最多保留 4096 条并采用 drop-oldest；`dropped_total > 0` 必须判失败并保存证据。当前已完成 FC20/FC21、G60V2/V3/V4 和 G2V2/G32V7 的本机同栈验证；Confirm 丢失、序号回绕、重发/重复等原始时序仍需独立故障注入和真实 EMS 验证。

推荐直接使用 `examples\pytest_ems\test_unsolicited_scenarios.py`：先在私有计划填写准确点号、Event Class/目标值和外部触发步骤，再把对应场景 `enabled` 改为 `true`。模板不会发送遥控来制造事件，会循环执行有界等待、精确匹配类型/索引/Event Group/Variation/值/可选时间戳，并保证在 `finally` 中 Disable。观察窗口内由批准的独立信号源按 `trigger_instructions` 改变输入。

### 6.4 大点表、性能与 24 小时稳定性

先把包内 `config\performance_profile.example.json` 复制为不提交 Git 的
`config\performance_profile.local.json`。不要只改点数总和：每个场景的 Header、
`expected_objects_per_iteration`、`expected_by_kind` 和
`expected_by_group_variation` 必须与正式 PICS/点表逐项一致；把 `scope` 改为
`TARGET_ENVIRONMENT_PENDING_REVIEW`，并由 EMS 负责人批准延迟、资源增长、重连和
磁盘阈值。示例阈值只用于本机工具回归，不能直接作为 EMS 验收标准。

公共示例为了日常运行速度只配置 BI/AI/BOS/AOS 各 1,024 点；仓库自动化边界
回归实际覆盖各 4,096 点，共 16,384 点。这个数字仍只是同机同栈工具证据，不能
直接写进真实 EMS 验收要求。

把 `examples\pytest_performance` 整体复制到既有框架后，先只跑有界 benchmark：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  .\third_party\ems_dnp3\examples\pytest_performance\test_read_performance.py -v `
  --dnp3-host-exe ".\third_party\ems_dnp3\bin\dnp3-master-host.exe" `
  --dnp3-pics-file ".\config\ems.local.json" `
  --dnp3-capability-matrix ".\third_party\ems_dnp3\config\capability_matrix.csv" `
  --dnp3-performance-profile ".\config\performance_profile.local.json" `
  --dnp3-performance-report-dir ".\evidence\local\performance" `
  --dnp3-unknown-policy error `
  --dnp3-outstation-host "192.0.2.10" `
  --dnp3-outstation-port 20000 `
  --dnp3-master-address 1 `
  --dnp3-outstation-address 1024
```

确认 benchmark、Profile、磁盘空间和独占环境均通过人工评审后，才追加
`--dnp3-run-soak`。它会按照 Profile 中的 `target_duration_seconds` 运行只读任务；
示例值为 86,400 秒。Ctrl+C、watchdog、host 退出、证据上限或阈值失败都会留下
明确的非通过终态，不会和下一次运行合并。runner 会消费 1,024 条有界的 native
channel-event 队列来发现两次状态快照之间的短暂断线/重连；事件队列发生任何
drop 都会失败。Profile 中的 watchdog 还必须严格覆盖 begin、Read、end/drain
三段 RPC 的最大预算，加载器会在运行前拒绝过小配置。报告中的
`formal_dut_conclusion=false` 是刻意的：正式结论还需要独立参考端、PCAP/网络
字节、DUT 资源、测试机/电源/网卡身份和评审记录。

本地突发/定速事件发生器只驱动包内回环从站，不会对真实 EMS 写点；其严格输入是
`config\local_event_profile.example.json`。虽然底层发生器可表达最多 65,535 条，
高层 benchmark 单块严格限制为 4,096 条，并同时核验 capture 与 master
unsolicited 队列；长流必须拆成逐块对账的请求。完整字段、capture 真值和检查点说明见
`docs\PERFORMANCE_AND_SOAK_GUIDE.md`。

### 6.5 查看主从站的 DNP3 报文详情

不需要在 pytest 中读取 EXE 的 stderr，也不需要修改 C++。使用配套的新 host 和
Python 包，在连接前 `start_trace()`，业务操作后 `read_trace()`，结束时先断开、
停止 trace 再排空即可。可复制的 pytest 示例见 [报文 trace 指南](PROTOCOL_TRACE.md)。
默认关闭，原有用例无需改动；开启后可取得原始 HEX、协议字段和栈诊断，任何采集丢失
默认报错。其范围是 OpenDNP3 栈已接收/已编码的 DNP3 数据，不含网卡级 TCP/IP 或所有
损坏报文，不能替代 PCAP。原始报文含业务内容，不能直接上传 GitHub 或共享报告。

## 7. LAB 控制用例：默认锁定，授权后运行

本节只适用于 LAB。当前模拟器用户请用 [单配置入门套件](SIMULATOR_QUICKSTART.md)，
无需本节审批、身份、事故锁、单场景选择和强制恢复要求。

LAB 遥控和模拟量输出会改变设备状态，必须同时满足以下条件：

1. 用例带 `dnp3_state_changing` 标记。
2. PICS 中相关能力为 `SUPPORTED`。
3. 命令行显式加入 `--dnp3-allow-state-changing`。
4. 提供可审计的 `--dnp3-operator-id` 和 `--dnp3-dut-id`。
5. 私有场景计划中的准确场景已 `enabled=true`，`authorization_reference` 是真实批准工单，不是占位符。
6. 命令行用 `--dnp3-control-scenario` 精确选择本次唯一场景。
7. 目标是获批的隔离实验 EMS，点号、操作前值、目标值、反馈点和恢复值已经人工确认。
8. 厂商已确认每个 CROB 点的控制模型以及 FC3/FC4 Select-Operate 支持状态；不要把名称含糊的 “Activation Model” 自动解释成布尔锁存。

优先使用 `examples\pytest_ems\test_control_scenarios.py`，不要让新手直接复制无读回的单条控制代码。严格计划要求操作和恢复使用相同命令类型/索引，恢复期望必须等于基线；CROB 和四种 Group 41 类型会自动映射到准确的 PICS 能力 ID。

运行命令除第 6 节参数外，还需：

```text
--dnp3-control-scenario "exact-approved-scenario-id" `
--dnp3-allow-state-changing `
--dnp3-operator-id "your-name-or-ticket" `
--dnp3-dut-id "lab-ems-asset-id"
```

`--dnp3-control-scenario` 故意没有环境变量替代项，避免旧终端残留值意外选中控制；一次 pytest 调用只能选择一个场景。模板顺序固定为：操作前 Static Read -> 发送一次命令 -> 检查每点 Command Status -> 轮询业务反馈 -> 发送一次预批准恢复 -> 再次读回基线。只有操作后状态已经确认才发送恢复；状态不明时停止并要求人工读回，不会盲目恢复。插件会拒绝已授权的 pytest-xdist 状态改变测试，模板会拒绝同进程 rerun/repeat；外部仍必须保证没有第二个主站。

连接成功时 C++ host 生成一次性会话令牌，Python 客户端只在内存中保存，断开或进程退出即失效。它是防误操作联锁，不是身份认证、访问控制或 Secure Authentication 的替代品。

控制超时或 host 通信异常后执行状态可能不确定；框架不会自动重试，并会按 DUT 身份哈希写入跨进程事故锁、销毁 host 和清除令牌。新进程仍可做只读查询，但所有控制会在发包前被拦截。必须独立读回、记录证据，并用准确事故 ID、确认人和读回摘要显式归档后才能解除；绝对不要删除 `active/*.json`。完整流程和代码见 `docs/SAFETY_INCIDENT_RUNBOOK.md`。

pytest 默认把锁放在 `evidence/local/safety-incidents`。直接使用 `Dnp3MasterClient` 时，必须给 `HostProcessConfig` 配置 `safety_incident_directory`，否则控制会 fail-closed。当前 OpenDNP3 后端不支持 `DIRECT_OPERATE_NR`，请求会明确返回 `UNSUPPORTED_BY_BACKEND`，不会伪造成功。

## 8. 常用环境变量

以下为通用 CLI/env 接入方式。单配置模拟器入口以 `settings.local.json` 和配套运行包
为连接/host 来源，忽略旧的连接/host 环境变量；不要把两种配置方式混用。

除安全设计上要求逐次输入的 `--dnp3-control-scenario` 外，常用命令行参数可用环境变量替代：

| 环境变量 | 含义 |
|---|---|
| `DNP3_MASTER_HOST_EXE` | host EXE 路径 |
| `DNP3_PICS_FILE` | 本地 EMS PICS JSON |
| `DNP3_CAPABILITY_MATRIX` | 能力矩阵路径（无法自动找到时使用） |
| `DNP3_POINTS_FILE` | 严格只读点表 CSV |
| `DNP3_EMS_PLAN` | 严格 EMS 场景计划 JSON |
| `DNP3_EVIDENCE_DIR` | 脱敏 pytest 证据输出根目录 |
| `DNP3_SAFETY_INCIDENT_DIR` | 不确定控制结果的持久事故锁目录 |
| `DNP3_UNKNOWN_POLICY` | `xfail`、`skip` 或 `error` |
| `DNP3_OUTSTATION_HOST` / `DNP3_OUTSTATION_PORT` | EMS TCP 地址/端口 |
| `DNP3_MASTER_ADDRESS` / `DNP3_OUTSTATION_ADDRESS` | DNP3 链路地址 |
| `DNP3_CONNECT_TIMEOUT` | 建连超时（秒） |
| `DNP3_RETRY_MIN` / `DNP3_RETRY_MAX` | 自动重连退避（秒） |
| `DNP3_ALLOW_STATE_CHANGING` | `1/true/yes/on` 才解锁收集阶段 |
| `DNP3_OPERATOR_ID` / `DNP3_DUT_ID` | 控制操作审计标识 |

控制场景选择没有 `DNP3_CONTROL_SCENARIO` 环境变量；必须在每次获批运行的命令行中显式提供。

敏感环境变量不要打印进日志。执行后可关闭当前 PowerShell，或显式删除本进程环境变量。

## 9. 常见问题排查

| 现象 | 优先检查 |
|---|---|
| 找不到 Visual Studio/CMake | Build Tools 是否安装 C++ 工作负载、x64 MSVC 和 Windows SDK；重新打开 PowerShell |
| `No module named dnp3_master` | 是否安装 `python` 子目录，或 `PYTHONPATH` 是否指向 `python\src` |
| EXE 无法启动/缺 DLL | 路径是否为 x64 Release 包；安装 VC++ 2015–2022 Redistributable x64；检查杀毒软件隔离记录 |
| 卡在 `native.host_smoke` 超过 15 秒 | 当前版本会自动发送 `hello`/`shutdown`，并有 10 秒进程超时和 15 秒 CTest 上限；若仍卡住，通常是旧提交或旧 CTest 配置，先拉取最新代码并重新执行 `build.ps1`，不要用关闭终端输入作为长期方案 |
| `CONNECTION_TIMEOUT` | EMS 是否作为 TCP Server 监听、IP/端口/路由/防火墙是否正确；先不要尝试控制 |
| `RESPONSE_TIMEOUT` | TCP 可能已通，但链路地址、请求对象、EMS 状态或超时配置不匹配；控制出现该错误时还必须按不确定结果事故锁流程处置 |
| `NOT_CONNECTED` | fixture 是否成功连接；是否已提前断开或 host 已退出 |
| 用例显示 `xfailed` | PICS 未提供、能力缺失或为 `UNKNOWN`；查看 `-ra` 原因 |
| 正向用例被跳过 | PICS 将能力声明为 `NOT_SUPPORTED`，或能力矩阵中的框架状态仍为 `BLOCKED`/`UNSUPPORTED_BY_BACKEND`；用 `-ra` 查看准确原因 |
| `invalid DNP3 EMS test plan` | 检查未知/重复字段、点号、Event Class、timeout、控制恢复基线和授权占位符；配置错误发生在连接 EMS 前 |
| 控制用例显示 `no control selected` | 这是默认安全状态；只有获批后才传 `--dnp3-control-scenario <精确ID>` |
| `SAFETY_INTERLOCK` | 未按第 7 节完成全部解锁条件，或令牌已因断开而过期 |
| `SafetyIncidentConfigurationError` | 直接调用控制时未配置持久事故目录；配置后重试，原命令尚未发出 |
| `UnresolvedSafetyIncidentError` | 该 DUT 有未关闭的不确定结果；只读核对并按事故手册显式确认，禁止删锁或重发 |
| `SafetyIncidentPersistenceError` | 锁无法可靠落盘/读取；停止全部控制，保留现场并修复存储 |
| 命令返回但 `all_success=False` | 检查每个 `point_results[i].status`，不能只看整个批次 |
| 控制成功但反馈未到目标值 | 不要重发或盲目恢复；停止后续控制、人工读回设备实际状态并保存证据 |
| `QUEUE_OVERFLOW` | 先检查结构化 `error.details`，区分测量数、4096 条分片记录或 1024 条 IIN 观测窗口溢出。`summary` 只减少明细内存/JSON，不会取消 `max_measurements` 完整性上限；测量数不足时可经评审提高上限或拆分请求，分片/IIN 溢出时应缩小/拆分场景并保存诊断，任何情况都不能忽略数据丢失 |
| `OBJECT_UNKNOWN` IIN | PICS/对象组/变体可能与 EMS 不一致；保存原始 IIN 并停止扩大测试范围 |

排查顺序建议固定为：先运行本机自检，再只测 TCP 连接，再做一个只读范围点，最后才做总召/事件/控制。这样能快速定位是安装、网络、地址、PICS、点表还是 EMS 行为问题。

## 10. 更新项目时如何保护内网配置

本地文件使用以下名称，仓库已默认忽略：

```text
config\ems.local.json
config\points.local.csv
config\ems_test_plan.local.json
evidence\local\
secrets\
```

更新前确认工作区：

```powershell
git status
git pull --ff-only
```

更新后先运行 `doctor.ps1`，再通过 `release.ps1` 完成 clean commit 的构建、完整测试、
生命周期、确定性打包和迁移验收。`bin\build-info.json` 记录 host 版本、OpenDNP3
commit、Git commit、工作区状态、构建配置和能力矩阵哈希；正式证据只使用
`git_worktree_state=clean` 且 commit 与发布报告一致的构建。复制可移植 ZIP 时同时
保存 `.sha256`、`package-manifest.json`、`release-closure-report.json` 和目标机生成的
迁移兼容报告。

严禁提交或上传：IEEE 标准 PDF、EMS IP/账号/密钥、本地点表、本地 PICS、PCAP、生产日志和未经脱敏的报告。项目自带 `.gitignore` 只是最后一道防线，提交前仍必须检查 `git status`。

## 11. 什么时候才需要改 C++

只有以下情况才需要进入 `native/`：新增协议功能、OpenDNP3 公共 API 无法表达的对象/限定词、Serial/UDP/TLS 新承载、抓包/高性能聚合或修复 native host 缺陷。普通 EMS 点位断言、PICS 选择、测试数据和业务流程都应写在 Python/pytest 层。

修改 C++ 时必须同时更新对应单元/集成测试、`hello.capabilities`、`config/capability_matrix.csv`、协议文档和构建验证；不能因本机自测通过就把能力提升为 `VERIFIED_INTEROP` 或 `VERIFIED_CONFORMANCE`。

项目尚未完成的工作及内网 agent 的推荐执行顺序见 `docs/INTRANET_HANDOFF_REMAINING_TASKS.md`。
