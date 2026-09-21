# 模拟 EMS：一份配置运行 pytest 基本功能验证

适用：EMS 监听 TCP，pytest 主站主动连接；EMS 下接模拟设备，允许遥控遥调。
本入口不发送时间同步、Restart，不对接模拟器信号生成接口。真实设备继续用 LAB 流程。

## 1. 准备一次

需要 Windows x64、Python 3.10+、pytest 8.x/9.x、同版本 Python 包和原生 host。
使用已编译运行包时不需要 C++ 编译器。只有 GitHub 源码时，先按
[小白构建与移植指南](BEGINNER_MIGRATION_BUILD_USE_GUIDE.md) 构建 Release。
不要复制整个 `out/build/windows-msvc-release` 作为运行包，使用 `package.ps1` 的产物。
GitHub 的 clone / Download ZIP 只包含已提交的源码、示例和文档，不包含被忽略的
`out/`、EXE、虚拟环境或你填写的私有配置；自动定位不会替你编译或下载这些文件。

源码仓库根目录准备（运行包使用者直接看第 4 节）：

```powershell
# 尚无虚拟环境时先执行
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\python[test]"
.\scripts\build.ps1 -Preset windows-msvc-release
Copy-Item .\examples\pytest_simulator\settings.example.json .\examples\pytest_simulator\settings.local.json
```

只复制一次，不要覆盖已经填写的配置。`settings.local.json` 被 Git 忽略，打包清单也
不包含它。示例 `192.0.2.10` 是文档保留地址，必须替换。

## 2. 只编辑测试目录里的 settings.local.json

| 字段 | 填什么 |
|---|---|
| `connection.host` / `port` | **EMS 的监听 IP/端口**，不是 pytest 的监听地址 |
| `connection.local_adapter` | 默认 `0.0.0.0` 系统选路；多网卡时可指定 pytest 机器内网网卡 IP |
| `connection.master_address` | DNP3 主站链路地址，例如 1，不是 IP |
| `connection.outstation_address` | EMS 从站链路地址，例如 1024，必须匹配 EMS 配置 |
| `points` | BI、AI、BO 状态、AO 状态的实际 DNP3 索引，不是 Excel 行号 |
| `controls` | 命令索引、写入值、反馈点引用；命令索引和反馈索引可以不同 |
| `events` | 外部模拟器变化后期望收到的 BI/AI 值 |
| `runtime_root` | `null` 自动查找；分离移植时填运行包根目录，相对路径以配置文件目录为准 |

没有“主站监听端口”：本框架是 TCP Client，本地源端口由 Windows 分配。
只改 IP/端口能否通过，取决于示例链路地址、点号、反馈映射是否也恰好正确。
这些现场事实需要你填写，代码不能推断。

示例有 16 个测试：连接+DNP3 探针 1 个、静态点 4 个、Class 0 1 个、
Class 1/2/3 各 1 个、遥控开/关 2 个、遥调正/零/负值 3 个、BI/AI 事件各 1 个。
不适用的控制/事件可以从数组删除；空数组显示 skip，不冒充通过。
points、controls、events 各最多 128 项，class_counts 最多 3 项；此入口不是大点表压力工具。

对象固定为你的 EMS 操作约定：BI G1V2/G2V2，AI G30V5/G32V7，BO 状态 G10V2、
控制 G12V1，AO 状态 G40V3、控制 G41V3。静态逐点用 range16。
遥脉可增加 `kind: "AI"` 的点，不自动当作 DNP3 Counter。

- `points[].expected` 省略或 `null`：检查对象、点号、唯一性和读取成功；填值额外断言
  当前值。不要给会被后续控制改变的反馈点填写不变的静态期望。
- `required_flags` 可选，例如 `1` 要求 ONLINE 位有效且置位；不默认限制其他质量位。
- 二进制值必须是 JSON `true`/`false`，不是 `1`/`0`。模拟量必须有限；
  `tolerance` 为绝对误差，默认 `0.01`。
- 控制只发一次 FC5，不自动重试、不自动恢复。CROB 为 LATCH_ON/OFF，Count=1，
  On/Off Time=0；AO 为 Float32。确认后轮询静态反馈，默认期望等于写入值；
  不同反馈含义可填控制的 `expected`。负值是否允许由 EMS 决定，可修改示例值。
  `feedback_point` 必须引用对应 BO/AO 状态点；此精简入口不把 BI/AI 自动当作控制反馈。
- `class_counts` 中 `count: 0` 表示**严格空响应**，对应你的 EMS 专用约定，不是标准
  普遍要求，也不是“至少 0 条”。其他 EMS 可修改或清空数组。
- 重复键、未知字段、重复 ID/点号、错误引用、越界值、NaN/Infinity、超过 1 MiB 的
  配置在启动 host 前失败。Schema 为 `schemas/simulator-settings.schema.json`；
  跨字段校验以 `load_simulator_settings()` 为准。

## 3. 分步运行

在源码仓库根目录运行；`-c` 保证采用该目录配置，无需一长串环境变量：

```powershell
# 连接和只读探针，不控制
.\.venv\Scripts\python.exe -m pytest -c .\examples\pytest_simulator\pytest.ini -k test_00 -v

# 静态和 Class 读取，不控制、不等外部信号
.\.venv\Scripts\python.exe -m pytest -c .\examples\pytest_simulator\pytest.ini -k "not control_and_feedback and not external_signal_event" -v

# 遥控遥调+反馈，无需审批开关
.\.venv\Scripts\python.exe -m pytest -c .\examples\pytest_simulator\pytest.ini -k control_and_feedback -v

# 外部变化；-s 实时显示准备完成提示
.\.venv\Scripts\python.exe -m pytest -c .\examples\pytest_simulator\pytest.ini -k external_signal_event -v -s

# 全部：会控制并等待事件
.\.venv\Scripts\python.exe -m pytest -c .\examples\pytest_simulator\pytest.ini -v -s
```

每个用例新建独立 host/连接，真正读取 `points[0]`，不是以 TCP 连通冒充 DNP3 成功。
Class 0 用 G60V1 单独读取，不混入 Class 1～3 总召。

### 自动定位和分层诊断是做什么的

Python 通过 `dnp3-master-host.exe` 发 DNP3 报文。自动定位只是帮你找到这个程序，
并检查 Python/host 版本、build-info 和能力矩阵配套，避免移植后误用旧程序。
这不是新的审批或控制限制，也不会修改 EMS 配置。

“TCP 连通”只说明网络连接建立了，不代表 EMS 接受 DNP3 请求，更不代表点号正确。
因此用例继续发起只读探针；控制用例还会检查命令结果和静态读回。
例如遥控开启获得成功响应，但 BO 状态始终为关闭，会在反馈层失败，提示检查映射或
模拟器状态；不会把“命令发出”当作功能验证通过，也不会自动重发命令。

| 错误前缀 | 优先检查 |
|---|---|
| `INSTALLATION` / `HOST_START` | EXE、build-info、能力矩阵配套，Python/host 版本，Windows 运行库 |
| `TCP_CONNECT` | EMS 监听、IP/端口、路由、防火墙、本地网卡 |
| `DNP3_READ` | 两个链路地址、EMS 协议配置、请求错误 IIN |
| `POINT_MAPPING` / `CLASS_ZERO` | 索引、Group/Variation、点类型、Class 0 是否包含此点 |
| `CLASS_COUNT` | Class 1/2/3 实际返回数量与配置是否一致，包括严格空响应 |
| `QUALITY` / `STATIC_VALUE` | 质量位或静态期望与模拟器状态是否相符 |
| `CONTROL_RESULT` / `CONTROL_FEEDBACK` | 命令状态、反馈映射、单位与误差；不会自动重发 |
| `EVENT_*` | Enable/Disable 支持、事件源、会话、丢失、时间字段、观察超时 |

底层异常保留原始异常链和结构化错误码；前缀是定位层级，不保证具体根因。
不是所有底层异常都重新包装成上表前缀：例如控制 RPC 超时仍会保留客户端原异常。
程序判断 `SimulatorCheckError` 可读 `.stage`；判断 `HostCommandError` 读 `.code` 和
`.details`，不要匹配人类可读的错误文本。
不确定控制仍关闭当前会话并报错；下一用例用新会话，无 LAB 事故锁。
`task_timeout` 0.1～60 秒，`feedback_timeout` 0.1～300 秒，`event_timeout` 0.1～3600 秒。
这不是整个测试的墙钟上限：另有启动/连接、探针、基线和 Enable/Disable，以及每任务
1 秒 RPC 余量；最后一次反馈读最多多 0.1 秒最小任务预算。所有等待有界。

### 配合外部模拟器产生事件

默认 `require_change=true`：读取基线后启用 Class，出现 `DNP3 EVENT READY` 后，
在外部模拟器改变点值到 `events[].expected`。BI 示例 false→true；AI 非 42.0→42.0。
下一用例重建连接并取基线，重复运行前注意重设初值。只验证“收到某值事件”可设
`require_change=false`，但它不证明变化。

默认要求绝对时间字段存在，但不校准 EMS 时钟、不验证时间同步精度。检查同会话、
队列无丢失、正确对象/索引、期望值，以及相对基线差异。
旧缓存事件可能在启用后到达；没有外部触发序号/真值，不能严格证明触发因果、重复/
遗漏总量或端到端时延。本入口不会伪造这种证明；完整性/性能继续使用已有 capture/soak。

## 4. 移植到已有 pytest 框架

1. 完整运行包放到目标工程 `third_party/ems_dnp3`，包含 bin、config、Python wheel 等。
2. 用目标工程 Python 安装 wheel，不需要编译 C++：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install --no-index --no-deps .\third_party\ems_dnp3\python-dist\dnp3_master_test_framework-0.6.1-py3-none-any.whl
   ```

   预先安装 pytest；离线机器提前准备 pytest 及依赖 wheel。同版本旧代码需显式加
   `--force-reinstall`，同时替换配套运行包。
3. 复制整个 `examples/pytest_simulator` 到目标 `tests/dnp3`，从示例创建当地配置。
   设置 `runtime_root` 为 `../../third_party/ems_dnp3`，填 EMS 和点号。
   JSON 路径推荐用 `/`，例如 `D:/TestProject/third_party/ems_dnp3`；使用反斜杠时
   必须写成 `\\`。`runtime_root` 指向含 bin/config 的目录，不是 EXE 或 ZIP 文件。
4. 目标工程根目录执行：

   ```powershell
   .\.venv\Scripts\python.exe -m pytest -c .\tests\dnp3\pytest.ini -v -s
   ```

该命令只跑此套件，无需改原框架全局配置。并入全工程收集时，在原 pytest.ini
现有 `addopts` 添加 `-p dnp3_master.pytest_plugin`（不覆盖其他选项），另加
`dnp3_simulator=true`、`dnp3_simulator_settings=tests/dnp3/settings.local.json`。
不要把全局模拟器模式与真实 LAB 测试混跑；同一 EMS 串行执行，避免控制值互相覆盖。

CLI `--dnp3-simulator-settings` 路径相对当前目录；ini `dnp3_simulator_settings`
相对 ini 文件。此入口以配置文件及其 runtime 为连接/host 唯一来源，忽略旧连接/host
环境变量；同时传连接/host CLI 参数会报错。其他插件选项（PICS、证据等）正常生效，
明确 NOT_SUPPORTED 仍跳过正向用例。已有 `DNP3_SIMULATOR=false` 会被明确拒绝，
需清除此旧设置或显式传 `--dnp3-simulator`。

自动发现只查配置文件向上最多 8 层的 `bin/dnp3-master-host.exe` 或源码 Release
路径，并要求同根能力矩阵和 EXE 邻接 build-info 匹配。不搜索 PATH、不下载依赖；
近层安装不匹配即失败，不偷偷回退旧 host。Debug/ASan 可显式整理为 bin 运行目录。
自动定位失败时：源码用户先构建 Release；运行包用户检查是否完整解压，再指定
`runtime_root`。校验不匹配应重新构建/安装同一批文件，不要删除版本或哈希检查。

公开 Python 辅助接口位于 `dnp3_master.simulator_suite`。fixture
`dnp3_simulator_settings` 返回冻结类型模型；也可复用 `dnp3_host_config`、
`dnp3_connection_config`，普通测试继续调用 `Dnp3MasterClient` 公共 API。
传 `--dnp3-evidence-dir <本地目录>` 可记录私有配置哈希而非内容，并标明 SIMULATOR。
pytest 失败回溯仍可能包含现场参数，外发前人工检查。

### 需要在用例中查看 DNP3 报文时

按 [报文 trace 指南](PROTOCOL_TRACE.md) 使用 `dnp3_host_config` 和
`dnp3_connection_config` 创建自己的 client，在 connect 前开启 trace，操作间读取，
断开后停止并排空。单配置入门套件本身不会默认收集或保存原始报文，也没有新增 JSON
开关；现有 settings 文件无需改变。trace 含业务载荷，且不是网卡级抓包，不能直接上传
共享日志。原来的静态/控制/事件断言与 SIMULATOR 模式保持不变。

## 5. 内网仍需要提供什么

现场配置和实际验收：IP/端口/链路地址、点号/反馈关系、合适的写入值、外部信号变化、
EMS 的 Class 空读与 Enable/Disable 实际行为。出现差异保留脱敏错误层级及协议错误码，
不要靠删除断言通过。FC6、32 位 Qualifier 不在此入口；时间同步和 Restart 已明确不需要。
本机同栈测试不是 EMS 已调通或 IEEE 一致性认证，24 小时结论仍需目标环境执行。
