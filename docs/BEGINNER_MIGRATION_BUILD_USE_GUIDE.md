# EMS_DNP3 拉取、构建、移植与使用指南（Windows/pytest 小白版）

本文面向不熟悉 C++ 的测试开发人员。正常使用时，你只需要写 Python/pytest；C++ 已封装在 `dnp3-master-host.exe` 中，不需要在测试代码里调用 OpenDNP3，也不需要理解 C++ 指针或编译器细节。

> 当前版本：0.2.0，目标平台 Windows x64，固定协议栈 OpenDNP3 3.1.2。当前实现已完成本机 TCP、Read/Class Poll、测量值/IIN、CROB 和四种 Analog Output 控制的同栈回归，但尚未代表真实 EMS 互操作或 IEEE 一致性认证。

## 1. 先理解四个目录

| 目录/文件 | 用途 | 普通 pytest 开发是否要改 |
|---|---|---|
| `python/src/dnp3_master/` | 给 pytest 使用的 Python 包、fixture、数据模型 | 通常只调用，不修改 |
| `bin/dnp3-master-host.exe` | Python 与 DNP3 网络之间的 C++ 宿主进程 | 不修改 |
| `config/` | 能力矩阵和 EMS PICS 示例 | 复制示例并填写本地值 |
| `schemas/` | JSON 协议和 EMS Profile 格式校验 | 不修改 |

工作链路是：

```text
pytest 用例 -> dnp3_master Python 包 -> dnp3-master-host.exe -> TCP/DNP3 -> EMS
```

因此，日常移植的核心是“复制 Python 包和已经编译好的 EXE”，不是把 C++ 源码嵌进原框架。

## 2. 两种使用路线

### 路线 A：在内网从源码构建

适合内网允许安装 Visual Studio Build Tools，并且后续需要修改 C++ 的情况。按第 3～6 节操作。

### 路线 B：在外网构建可移植包，再拷入内网

适合内网机器只运行 pytest、不修改 C++ 的情况。先在有构建工具的 Windows x64 机器执行：

```powershell
cd D:\Work\Code\EMS_DNP3
.\scripts\package.ps1 -Preset windows-msvc-release -Force
```

产物位于：

```text
out\package\ems-dnp3-pytest-0.2.0\
```

将这个目录整体复制到内网。包中包含主程序、只用于本机自检的测试从站、Python 源码、Schema、能力矩阵、依赖锁、许可证和本文档，不包含 IEEE 标准 PDF、EMS 本地配置、抓包或密钥。

内网目标机器若不安装 Build Tools，通常仍需安装 Microsoft Visual C++ 2015–2022 Redistributable x64 和 Python 3.10 或更高版本。

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

## 4. 一键构建、测试和本机自检

在仓库根目录执行：

```powershell
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

## 5. 最简单的移植方式：整包复制

建议把第 2 节生成的整个目录复制到既有框架，例如：

```text
你的自动化项目\
  third_party\ems_dnp3\
    bin\dnp3-master-host.exe
    python\src\dnp3_master\
    config\
    schemas\
    ...
```

然后在既有框架的虚拟环境中安装 Python 层：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".\third_party\ems_dnp3\python"
```

如果不能执行 pip，则在运行 pytest 前设置：

```powershell
$env:PYTHONPATH = "$PWD\third_party\ems_dnp3\python\src"
```

在既有框架根目录的 `conftest.py` 增加：

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

如果已有 `pytest_plugins`，把字符串追加到原元组中，不要再定义第二个同名变量。

### 5.1 最小复制清单

若不能整包复制，至少保留：

```text
python\src\dnp3_master\
bin\dnp3-master-host.exe
bin\build-info.json
config\capability_matrix.csv
config\ems_profile.example.json
schemas\
dependency-locks\
licenses\
NOTICE.txt
THIRD_PARTY_LICENSES.txt
```

`tools\dnp3-local-test-outstation.exe` 只用于本机自检；连接真实 EMS 时不需要，但建议保留以便快速区分“框架坏了”还是“EMS/网络配置有问题”。

## 6. 第一次连接真实 EMS（只读）

### 6.1 先准备 PICS 和连接参数

从 EMS 负责人取得以下信息，不能猜：

- EMS 厂商、型号、固件版本和 Device Profile/PICS 版本。
- EMS 是否作为 DNP3 Outstation/TCP Server 监听。
- IP、TCP 端口、本地主站链路地址、EMS 从站链路地址。
- 支持的对象组/变体、Class 分配、最大响应/分片、主动上送策略。
- 点表：类型、索引、Class、量程、是否可写。

仓库中的 `docs/standards/ems_device_profile.md` 已记录目前取得的“DNP3 操作约定”。它只是部分输入，不是正式 PICS：其中事件 Read、FC6 响应、SBO、CROB 点模型、广播和“遥脉同遥测”仍有冲突或歧义。先取得该文档 D01～D09 的厂商书面答复；未关闭的能力必须保留 `UNKNOWN`，不要通过连接真实设备试错来猜遥控含义。

复制 PICS 示例到被 Git 忽略的本地文件：

```powershell
Copy-Item .\config\ems_profile.example.json .\config\ems.local.json
```

只根据正式 Device Profile/PICS 或经批准的项目决定，把每项填写为：

- `SUPPORTED`：EMS 声明支持，正向用例可以运行。
- `NOT_SUPPORTED`：EMS 声明不支持，正向用例跳过，可运行专门的不支持行为用例。
- `UNKNOWN`：证据不足；默认 `xfail(run=False)`，不会触碰 EMS。

不要为了让测试“变绿”而把 `UNKNOWN` 改成 `SUPPORTED`。

插件会把 PICS 中的每个能力 ID 与 `config\capability_matrix.csv` 交叉检查，拼错或不存在的 ID 会在收集阶段直接报错。整包/推荐目录无需额外参数；如果你改变了目录布局，请同时传入 `--dnp3-capability-matrix ".\third_party\ems_dnp3\config\capability_matrix.csv"`。

### 6.2 写一个只读 pytest 用例

```python
import pytest


@pytest.mark.dnp3_dut
@pytest.mark.dnp3_capability("APP.FC.01.READ")
def test_ems_integrity_read(connected_master):
    result = connected_master.integrity_poll(
        timeout=10.0,
        max_measurements=100_000,
        return_mode="detail",
    )
    assert result.task_status == "SUCCESS"
    assert result.iin["raw_hex"]
    assert result.summary["received_total"] == len(result.measurements)
```

运行时显式传入本地参数：

```powershell
.\.venv\Scripts\python.exe -m pytest .\tests\test_ems_read.py -v `
  --dnp3-host-exe ".\third_party\ems_dnp3\bin\dnp3-master-host.exe" `
  --dnp3-pics-file ".\config\ems.local.json" `
  --dnp3-unknown-policy error `
  --dnp3-outstation-host "192.0.2.10" `
  --dnp3-outstation-port 20000 `
  --dnp3-master-address 1 `
  --dnp3-outstation-address 1024
```

将示例 IP 和地址换成实验 EMS 的真实参数。`--dnp3-unknown-policy error` 适合正式执行，可防止因 PICS 漏填而悄悄跳过。

范围读取示例：

```python
from dnp3_master import ReadHeader


def test_one_analog_point(connected_master):
    result = connected_master.read(
        [ReadHeader.range16(group=30, variation=0, start=0, stop=0)],
        timeout=5.0,
    )
    analogs = result.measurements_of_kind("analog_input")
    assert len(analogs) == 1
    assert analogs[0].index == 0
```

还可调用 `class_poll((1, 2, 3))`，或一次向 `read([...])` 传入最多 64 个严格校验的 Header。大量点优先使用 `return_mode="summary"`，避免在结果中保留和传输巨大的逐点 JSON。当前 EMS 约定声称事件/Class 1～3 Read 恒为空，这与标准事件轮询存在差异；只有在“确认无事件”和“人工产生已知事件”两个场景都保存证据后，才能形成 DUT 结论。

## 7. 控制用例：默认永久锁住，只有实验室可解锁

遥控和模拟量输出会改变设备状态。必须同时满足以下条件：

1. 用例带 `dnp3_state_changing` 标记。
2. PICS 中相关能力为 `SUPPORTED`。
3. 命令行显式加入 `--dnp3-allow-state-changing`。
4. 提供可审计的 `--dnp3-operator-id` 和 `--dnp3-dut-id`。
5. 目标是获批的隔离实验 EMS，点号和值已经人工确认。
6. 厂商已确认每个 CROB 点的控制模型以及 FC3/FC4 Select-Operate 支持状态；不要把名称含糊的 “Activation Model” 自动解释成布尔锁存。

示例仅展示写法，`CONTROL_POINT_FROM_APPROVED_POINT_LIST` 必须替换为获批点表中的点号：

```python
import pytest
from dnp3_master import CrobCommand


@pytest.mark.dnp3_dut
@pytest.mark.dnp3_capability("APP.FC.03.SELECT")
@pytest.mark.dnp3_capability("APP.FC.04.OPERATE")
@pytest.mark.dnp3_state_changing
def test_authorized_crob_sbo(connected_master):
    point = CONTROL_POINT_FROM_APPROVED_POINT_LIST
    result = connected_master.select_and_operate(
        [CrobCommand(index=point, operation="latch_on")],
        timeout=10.0,
    )
    assert result.task_status == "SUCCESS"
    assert result.all_success
    assert all(item.status == "SUCCESS" for item in result.point_results)
```

运行命令除第 6 节参数外，还需：

```powershell
--dnp3-allow-state-changing `
--dnp3-operator-id "your-name-or-ticket" `
--dnp3-dut-id "lab-ems-asset-id"
```

连接成功时 C++ host 生成一次性会话令牌，Python 客户端只在内存中保存，断开或进程退出即失效。它是防误操作联锁，不是身份认证、访问控制或 Secure Authentication 的替代品。

控制超时后执行状态可能不确定；框架不会自动重试。必须先查 EMS 状态和点位读回，再由负责人决定是否发起一个新的操作。当前 OpenDNP3 后端不支持 `DIRECT_OPERATE_NR`，请求会明确返回 `UNSUPPORTED_BY_BACKEND`，不会伪造成功。

## 8. 常用环境变量

命令行参数均可用环境变量替代：

| 环境变量 | 含义 |
|---|---|
| `DNP3_MASTER_HOST_EXE` | host EXE 路径 |
| `DNP3_PICS_FILE` | 本地 EMS PICS JSON |
| `DNP3_CAPABILITY_MATRIX` | 能力矩阵路径（无法自动找到时使用） |
| `DNP3_UNKNOWN_POLICY` | `xfail`、`skip` 或 `error` |
| `DNP3_OUTSTATION_HOST` / `DNP3_OUTSTATION_PORT` | EMS TCP 地址/端口 |
| `DNP3_MASTER_ADDRESS` / `DNP3_OUTSTATION_ADDRESS` | DNP3 链路地址 |
| `DNP3_CONNECT_TIMEOUT` | 建连超时（秒） |
| `DNP3_RETRY_MIN` / `DNP3_RETRY_MAX` | 自动重连退避（秒） |
| `DNP3_ALLOW_STATE_CHANGING` | `1/true/yes/on` 才解锁收集阶段 |
| `DNP3_OPERATOR_ID` / `DNP3_DUT_ID` | 控制操作审计标识 |

敏感环境变量不要打印进日志。执行后可关闭当前 PowerShell，或显式删除本进程环境变量。

## 9. 常见问题排查

| 现象 | 优先检查 |
|---|---|
| 找不到 Visual Studio/CMake | Build Tools 是否安装 C++ 工作负载、x64 MSVC 和 Windows SDK；重新打开 PowerShell |
| `No module named dnp3_master` | 是否安装 `python` 子目录，或 `PYTHONPATH` 是否指向 `python\src` |
| EXE 无法启动/缺 DLL | 路径是否为 x64 Release 包；安装 VC++ 2015–2022 Redistributable x64；检查杀毒软件隔离记录 |
| `CONNECTION_TIMEOUT` | EMS 是否作为 TCP Server 监听、IP/端口/路由/防火墙是否正确；先不要尝试控制 |
| `DNP3_RESPONSE_TIMEOUT` | TCP 可能已通，但链路地址、请求对象、EMS 状态或超时配置不匹配 |
| `NOT_CONNECTED` | fixture 是否成功连接；是否已提前断开或 host 已退出 |
| 用例显示 `xfailed` | PICS 未提供、能力缺失或为 `UNKNOWN`；查看 `-ra` 原因 |
| 正向用例被跳过 | PICS 将能力声明为 `NOT_SUPPORTED` |
| `SAFETY_INTERLOCK` | 未按第 7 节完成全部解锁条件，或令牌已因断开而过期 |
| 命令返回但 `all_success=False` | 检查每个 `point_results[i].status`，不能只看整个批次 |
| `QUEUE_OVERFLOW` | 提高经评审的 `max_measurements`，或改用 `return_mode="summary"`；不要忽略数据丢失 |
| `OBJECT_UNKNOWN` IIN | PICS/对象组/变体可能与 EMS 不一致；保存原始 IIN 并停止扩大测试范围 |

排查顺序建议固定为：先运行本机自检，再只测 TCP 连接，再做一个只读范围点，最后才做总召/事件/控制。这样能快速定位是安装、网络、地址、PICS、点表还是 EMS 行为问题。

## 10. 更新项目时如何保护内网配置

本地文件使用以下名称，仓库已默认忽略：

```text
config\ems.local.json
config\points.local.csv
evidence\local\
secrets\
```

更新前确认工作区：

```powershell
git status
git pull --ff-only
```

更新后重新构建、执行完整测试和本机自检。通过 `bin\build-info.json` 记录 host 版本、OpenDNP3 commit、Git commit、工作区状态、构建配置和能力矩阵哈希，运行报告应保存这份信息；正式证据只使用 `git_worktree_state` 为 `clean` 的构建。

严禁提交或上传：IEEE 标准 PDF、EMS IP/账号/密钥、本地点表、本地 PICS、PCAP、生产日志和未经脱敏的报告。项目自带 `.gitignore` 只是最后一道防线，提交前仍必须检查 `git status`。

## 11. 什么时候才需要改 C++

只有以下情况才需要进入 `native/`：新增协议功能、OpenDNP3 公共 API 无法表达的对象/限定词、Serial/UDP/TLS 新承载、抓包/高性能聚合或修复 native host 缺陷。普通 EMS 点位断言、PICS 选择、测试数据和业务流程都应写在 Python/pytest 层。

修改 C++ 时必须同时更新对应单元/集成测试、`hello.capabilities`、`config/capability_matrix.csv`、协议文档和构建验证；不能因本机自测通过就把能力提升为 `VERIFIED_INTEROP` 或 `VERIFIED_CONFORMANCE`。

项目尚未完成的工作及内网 agent 的推荐执行顺序见 `docs/INTRANET_HANDOFF_REMAINING_TASKS.md`。
