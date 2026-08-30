# 可复制的真实 EMS pytest 场景套件

该目录可以整体复制进已有 pytest 工程。它包含四类模板：

- `test_read_points.py`：按严格点表逐点读取遥信、遥测、遥控状态、遥调状态等 Static 对象。
- `test_poll_scenarios.py`：按场景计划执行完整性扫描和 Class 1/2/3 Read。
- `test_unsolicited_scenarios.py`：显式启用主动上报，等待外部信号源制造的确定事件，并在 `finally` 中禁用。
- `test_control_scenarios.py`：仅运行命令行精确点名的一个控制场景，执行前读回、单次控制、控制后读回、单次恢复和恢复读回。

所有真实 DUT 用例都受 PICS 门禁约束。未提供配置时安全跳过；能力为 `UNKNOWN` 时默认 `xfail(run=False)`；所有主动上报和控制示例均默认关闭。

## 1. 准备文件

复制公开模板为不会提交的本地文件：

```powershell
Copy-Item .\config\ems_profile.example.json .\config\ems.local.json
Copy-Item .\config\points.example.csv .\config\points.local.csv
Copy-Item .\config\ems_test_plan.example.json .\config\ems_test_plan.local.json
```

必须从 EMS 负责人取得 PICS、固件身份、链路地址和点表，不能猜。三个本地文件的职责严格分离：

- `ems.local.json`：设备能力是 `SUPPORTED`、`NOT_SUPPORTED` 还是 `UNKNOWN`。
- `points.local.csv`：只读对象、索引、Class、工程范围和是否启用。
- `ems_test_plan.local.json`：完整性/Class、事件触发和经过审批的控制闭环。

加载器会在建立连接前拒绝重复键、未知字段、重复场景、错误 Class/点号、越界 timeout、无事件元数据的主动上报点、无法恢复原基线的控制，以及已启用但仍使用占位授权信息的控制。

## 2. 先只收集，不连接 EMS

```powershell
.\.venv\Scripts\python.exe -m pytest .\examples\pytest_ems --collect-only -q `
  --dnp3-pics-file .\config\ems.local.json `
  --dnp3-points-file .\config\points.local.csv `
  --dnp3-ems-plan .\config\ems_test_plan.local.json `
  --dnp3-unknown-policy error
```

任何 `UsageError` 都应先修配置。不要为了消除 `UNKNOWN` 而把没有证据的能力改成 `SUPPORTED`。

## 3. 运行只读基线

把地址替换为隔离实验 EMS 的真实值：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  .\examples\pytest_ems\test_read_points.py `
  .\examples\pytest_ems\test_poll_scenarios.py -v -ra `
  --dnp3-host-exe .\out\build\windows-msvc-release\bin\dnp3-master-host.exe `
  --dnp3-pics-file .\config\ems.local.json `
  --dnp3-points-file .\config\points.local.csv `
  --dnp3-ems-plan .\config\ems_test_plan.local.json `
  --dnp3-evidence-dir .\evidence\local `
  --dnp3-unknown-policy error `
  --dnp3-outstation-host "<LAB_EMS_IP>" `
  --dnp3-outstation-port 20000 `
  --dnp3-master-address 1 `
  --dnp3-outstation-address 1024
```

示例计划允许 Class Read 空响应，以兼容目前拿到的 EMS 操作约定。若 PICS/厂商确认某次操作必须返回事件，应填写 `minimum_measurements` 和 `expected_point_ids`，不能把任意空响应当成功。

## 4. 主动上报

先在本地计划中填写准确的事件点、Class、目标值和外部触发步骤，再把对应 `enabled` 改为 `true`。运行测试后，在观察窗口内按私有计划由独立信号源改变输入：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  .\examples\pytest_ems\test_unsolicited_scenarios.py -v -s `
  <第3节的全部连接、PICS、点表、计划和证据参数>
```

此测试不会通过遥控制造事件。它要求精确匹配点类型、索引、Event Group/Variation、目标值和可选时间戳，并在任何路径关闭 unsolicited；`dropped_total` 非零立即失败。

## 5. 控制：三道技术门且每次只能选一个

控制场景默认 `enabled=false`。只有书面授权、低风险实验点、联锁、初始值、目标值、反馈点和恢复动作全部复核后，才能在私有计划中填写真实 `authorization_reference` 并启用一个场景。

即使场景已启用，仍不会自动运行。命令行必须同时提供：

```powershell
--dnp3-control-scenario "<EXACT_SCENARIO_ID>" `
--dnp3-allow-state-changing `
--dnp3-operator-id "<APPROVED_OPERATOR_OR_TICKET>" `
--dnp3-dut-id "<STABLE_LAB_ASSET_ID>"
```

`--dnp3-control-scenario` 故意没有环境变量替代项，避免旧终端环境意外选择控制。完整命令还必须包含第 3 节的 PICS、点表、计划、证据和连接参数，并只运行 `test_control_scenarios.py`。插件会拒绝已授权的 xdist 并行控制，模板也会拒绝同一 pytest 进程中的 rerun/repeat；仍须保证外部没有第二个主站。

模板不会重试任何控制。只有操作结果明确成功且控制后反馈达到批准值，才会发送一次预批准恢复命令；反馈不一致时停止并要求人工读回，不会盲目恢复。控制结果超时或不确定时，核心客户端会写入持久事故锁、清除令牌并销毁 host，之后按 `docs/SAFETY_INCIDENT_RUNBOOK.md` 处置。

当前只支持有响应 SBO 和有响应 Direct Operate。FC6 `DIRECT_OPERATE_NR` 不在计划模型中，后端会明确返回 `UNSUPPORTED_BY_BACKEND`。
