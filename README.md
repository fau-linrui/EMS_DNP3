# DNP3 Windows Master Automation Test Framework

面向 Windows x64、IEEE 1815-2012 和 pytest 的可移植 DNP3 主站自动化测试框架。当前版本 0.6.1，固定使用 OpenDNP3 3.1.2，并支持完全离线的 C++ 构建。

普通测试开发只使用 Python/pytest；C++ 协议栈封装在独立的 `dnp3-master-host.exe` 中：

```text
pytest -> dnp3_master Python package -> NDJSON -> dnp3-master-host.exe
       -> OpenDNP3 3.1.2 TCP Client -> EMS Outstation
```

## 当前能力

- TCP Client 单会话连接、断开、连接超时、退避重连和有界状态事件。
- 总召、Class 1/2/3 Poll、范围/计数/最多 64 Header 的 Read。
- BI、DBBI、BOS、Counter、Frozen Counter、Analog、AOS、Octet String、Time-and-Interval 等公开测量回调的类型化交付。
- EMS 约定所需 G1V2/G2V2、G30V5/G32V7、G10V2、G40V3 和 G60V1～V4 的精确本机覆盖。
- 索引、原始 flags、时间、接收顺序、IIN 原始值/解析位、任务状态/耗时和 detail/summary 有界结果；测量、分片或当前任务 IIN 丢失都会明确失败。
- 显式 Enable/Disable Unsolicited、跨请求持续接收、会话/分片/顺序标识和 4096 条 drop-oldest 有界队列。
- `capture.begin/progress/end` 持续有界采集；静态点集检查 missing/duplicate/unmatched，确定性事件流使用外部 manifest 和有序 SHA-256 真值。
- 可扩展至每类 65,535 点的回环从站、确定性突发/定速事件发生器，以及 16,384 点大总召的 capture A/B 性能回归；本机事件基准以最多 4,096 条的有界块同时核验 capture 与 master unsolicited 队列，任一层丢失都失败。
- 严格性能 Profile/报告、Windows host CPU/内存/句柄/线程采样和可中断 24 小时 read-only soak；原子检查点、哈希链、轮转、有界 watchdog、无丢失通道事件重连审计、磁盘与证据上限均 fail closed。
- CROB Select-Before-Operate、有响应 Direct Operate、四种 Analog Output 和逐点 IEEE 1815-2012 Command Status 视图；固定栈对未知线上状态 19～125 的折叠会以歧义标志显式暴露。
- pytest PICS 三态选择与框架能力双门禁、严格点表、实际限定符依赖、严格 EMS 场景计划、脱敏证据清单和状态改变安全门。
- 可整体复制的真实 EMS pytest 套件：逐点 Static Read、完整性/Class Poll、外部触发的主动上报观察，以及控制前后读回和恢复闭环。
- 只监听 `127.0.0.1` 的可编程有状态测试从站，以及经过真实 Python/native/OpenDNP3 链路的 Static/Poll/Unsolicited/Control 场景回归。
- PICS、点表、EMS 场景计划和能力矩阵的离线交叉预检；输出逐能力 blocker、输入 SHA-256 和机器可读 JSON，且不会连接 DUT。
- 不确定控制结果的跨进程 DUT 事故锁、只读核对和显式读回确认归档；点级 TIMEOUT、2012 保留状态、raw 127 歧义或结果错配均会销毁会话。
- Python/host 版本、固定 OpenDNP3 版本及 pytest 能力矩阵 SHA-256 启动握手，防止混用旧产物。
- 环境体检、clean-commit 发布门禁、可复现 wheel/ZIP/SHA-256/逐文件清单、包含 8 点精确静态 capture 的解包回环自检、空白 pytest 消费者迁移验收、Debug/Release/ASan 和 1,000 次生命周期验收入口。

控制默认锁住。只有获批实验室运行显式提供允许开关、operator ID、DUT ID，并连接时取得一次性会话令牌后才能调用。控制超时不会自动重试；任何不确定结果都会销毁会话并留下持久事故锁，必须独立读回和显式确认。当前 `DIRECT_OPERATE_NR` 明确返回 `UNSUPPORTED_BY_BACKEND`。

> 重要：当前 DNP3 端到端回归的主站和测试从站都使用同一 OpenDNP3 版本，只是本机工程验证，不是与真实 EMS 的互操作结论，也不是 IEEE 一致性认证。能力矩阵中的对应状态因此保持 `IMPLEMENTED_UNVERIFIED`。

## 环境

- Windows x64
- Visual Studio 2022 Build Tools（MSVC v143 x64、Windows SDK）
- CMake 3.25+
- Python 3.10+
- pytest 8.x 或 9.x

构建脚本会自动定位 Visual Studio 工具链。OpenDNP3、Asio、exe4cpp、ser4cpp 和 nlohmann/json 均已固定版本、摘要和许可证；正常 CMake 构建不访问网络。

## 快速开始

```powershell
git clone git@github.com:fau-linrui/EMS_DNP3.git
cd EMS_DNP3
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\python[test]"
.\scripts\doctor.ps1
.\scripts\build.ps1 -Preset windows-msvc-release
.\scripts\test.ps1 -Preset windows-msvc-release
.\scripts\run-local-self-test.ps1 -Preset windows-msvc-release
```

Release 主程序位于：

```text
out\build\windows-msvc-release\bin\dnp3-master-host.exe
out\build\windows-msvc-release\bin\build-info.json
```

正式生成可直接复制到内网/既有 pytest 项目的包（要求代码已提交且工作区干净）：

```powershell
.\scripts\release.ps1 -LifecycleIterations 1000
```

该入口执行 Release 全量测试、1,000 次生命周期、两次确定性打包和空白 pytest
迁移验收；报告位于 `out\release`。产物目录、可安装 wheel、确定性 ZIP 和 SHA-256
校验文件位于 `out\package\ems-dnp3-pytest-0.6.1*`。`package.ps1` 默认也会拒绝
dirty/stale build；`-AllowNonCleanBuild` 只能用于本地检查，产物不得发布。包不会包含
本地 IEEE 标准 PDF、EMS PICS、点表、场景计划、PCAP 或密钥。

## 集成到现有 pytest

推荐复制整个可移植包，并从包内 `python-dist\*.whl` 安装，避免 pip 改写受清单
保护的源码目录。安装后先执行包根的 `compatibility-test.ps1`，它会在临时空白
pytest 项目中完成离线隔离安装、插件和本机回环验收；然后在目标框架根
`conftest.py` 启用插件：

```python
pytest_plugins = ("dnp3_master.pytest_plugin",)
```

只读用例示例：

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
def test_integrity(connected_master):
    result = connected_master.integrity_poll(timeout=10.0)
    assert result.task_status == "SUCCESS"
    assert _REQUEST_ERROR_IIN_BITS.isdisjoint(result.iin["bits"])
    assert result.iin["observation_window_dropped"] == 0
```

手写 DUT 用例必须为实际功能码、对象和 Qualifier 分别声明全部能力 ID；插件不会从任意测试函数体中猜测依赖。上例的完整性扫描实际请求 G60V1～V4/Q06，因此不能只标记 FC1。优先复制 `examples/pytest_ems`，其参数化场景会从严格点表/计划自动附加准确依赖。

可直接复制 `examples/pytest_ems`，再从 `config/ems_test_plan.example.json` 建立私有计划。计划内的完整性/Class 场景可只读执行；主动上报和控制示例默认关闭。控制即使在计划中启用，也必须逐次用 `--dnp3-control-scenario` 精确点名，并同时通过 PICS、pytest 状态改变授权和 host 会话令牌门。

填写三个私有配置后，应先离线预检；只有退出码为 0 才进入 DUT 测试准备：

```powershell
.\.venv\Scripts\python.exe -m dnp3_master.preflight `
  --pics .\config\ems.local.json `
  --points .\config\points.local.csv `
  --plan .\config\ems_test_plan.local.json `
  --capability-matrix .\config\capability_matrix.csv
```

真实 EMS 用例必须从未提交的本地 PICS、点表、场景计划和连接参数驱动。当前已取得一份部分“DNP3 操作约定”，但因缺固件身份且事件、FC6、SBO、CROB 模型、广播和遥脉映射仍待澄清，不能直接解锁 DUT 测试。详细命令、控制安全示例和排错方法见下方文档。

## 文档入口

- [版本变更记录](CHANGELOG.md)
- [小白拉取、构建、移植与使用指南](docs/BEGINNER_MIGRATION_BUILD_USE_GUIDE.md)
- [干净版本发布与 pytest 迁移验收](docs/RELEASE_AND_MIGRATION_ACCEPTANCE.md)
- [内网交接与剩余任务卡](docs/INTRANET_HANDOFF_REMAINING_TASKS.md)
- [Python 客户端与 pytest 集成](docs/python_client.md)
- [H08/H09 持续采集、性能、大点表与 24 小时指南](docs/PERFORMANCE_AND_SOAK_GUIDE.md)
- [EMS 私有配置离线预检](docs/OFFLINE_PREFLIGHT.md)
- [本机可编程 DNP3 测试从站](docs/LOCAL_TEST_OUTSTATION.md)
- [不确定控制结果事故锁处理手册](docs/SAFETY_INCIDENT_RUNBOOK.md)
- [Host NDJSON 协议](docs/protocol.md)
- [架构说明](docs/architecture.md)
- [EMS 操作约定、PICS 状态与待确认偏差](docs/standards/ems_device_profile.md)
- [IEEE/OpenDNP3 状态与输入缺口](docs/standards/inputs_checklist.md)
- [能力矩阵](config/capability_matrix.csv)

## 安全与发布边界

- `config/ems.local.json`、`config/points.local.csv`、`config/ems_test_plan.local.json`、`secrets/`、`evidence/local/`、PCAP、密钥和本地标准 PDF 均被忽略，仍需在提交前人工检查 `git status`。
- 本地会话令牌只是防误操作联锁，不替代认证、权限管理或 Secure Authentication v5。
- 未实现能力必须返回稳定错误，禁止空实现或假成功。
- 只有独立互操作/一致性证据齐全时，才能提升能力矩阵中的验证状态。
