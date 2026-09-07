# EMS 私有配置离线预检

本入口保留 LAB 的正式身份/PICS 完备性检查，不是纯模拟器的运行门禁。
模拟设备可直接使用 [模拟器模式](SIMULATOR_MODE.md)，由 pytest 校验点表和场景，
无需先取得本工具的 READY，也不需为绕过预检伪造审批或 PICS。

`python -m dnp3_master.preflight` 在建立任何 TCP 连接之前，交叉检查四个输入：

1. EMS Device Profile/PICS JSON；
2. 私有点表 CSV；
3. 私有 EMS 场景计划 JSON；
4. 本版本的 `capability_matrix.csv`。

它只回答“配置是否完整、自洽，而且本版本框架是否实现了所有被启用场景所需能力”。`READY` 不代表真实 EMS 已通过互操作或 IEEE 一致性验证。

## 运行命令

在源码仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m dnp3_master.preflight `
  --pics .\config\ems.local.json `
  --points .\config\points.local.csv `
  --plan .\config\ems_test_plan.local.json `
  --capability-matrix .\config\capability_matrix.csv
```

在已移植的 pytest 项目根目录，矩阵通常位于包内：

```powershell
$packageRoot = (Resolve-Path '.\third_party\ems_dnp3').Path
.\.venv\Scripts\python.exe -m dnp3_master.preflight `
  --pics .\config\ems.local.json `
  --points .\config\points.local.csv `
  --plan .\config\ems_test_plan.local.json `
  --capability-matrix (Join-Path $packageRoot 'config\capability_matrix.csv')
```

命令不会读取 IP/端口，也不会启动 host 或连接 DUT。

## 退出码

| 退出码 | 含义 | 是否可继续连接 DUT |
|---|---|---|
| `0` | `READY`：离线配置门通过 | 可进入审批后的下一步；仍不是互操作通过 |
| `2` | `INVALID`：文件格式、引用或边界错误 | 不可 |
| `3` | `NOT READY`：格式有效，但存在占位符、PICS 非 SUPPORTED 或框架缺口 | 不可 |

仓库提供的示例故意保留 `FILL_ME` 和 `UNKNOWN`，因此退出码应为 3。这是正确的安全行为，不是工具故障。

## 判定规则

预检按启用内容推导最低能力集合：

- 基础连接和读：`CHANNEL.TCP.CLIENT`、`APP.FC.01.READ`；
- 每个启用点的精确 Static 对象；
- 每个启用 Integrity/Class 场景的 G60、Class 和期望 Event 对象；
- 每个启用主动上报场景的 FC20、FC21、FC130、Class 和 Event 对象；
- 每个启用控制场景的 FC3/FC4 或 FC5、G12/G41、反馈对象及 Command Status。
- 每条实际请求的限定符：逐点 Q00/Q01、Integrity/Class/unsolicited 控制 Q06、命令索引 Q17/Q28。

每项必须同时满足：

- 私有 PICS 状态为 `SUPPORTED`；
- 能力矩阵的框架状态至少为 `IMPLEMENTED_UNVERIFIED` 或某个 `VERIFIED_*` 状态。

`UNKNOWN`、缺失、`NOT_SUPPORTED`、`PLANNED`、`BLOCKED` 或后端不支持都会形成 blocker。未启用主动上报或控制只产生 warning，因为先做纯只读测试是允许的。设备 vendor/model/firmware/profile revision 中仍含 `FILL_ME/TODO/TBD/PLACEHOLDER/EXAMPLE` 会形成 blocker。

固定 OpenDNP3 3.1.2 公共 API 不支持 Q02/Q09/Q39 的 32-bit range/count/index；矩阵明确标为 `UNSUPPORTED_BY_BACKEND`。仅在 PICS 中写 `SUPPORTED` 不会解锁这些路径，也不会自动降级到 8/16-bit。

## JSON 报告与审计

加入 `--json` 可得到机器可读报告：

```powershell
.\.venv\Scripts\python.exe -m dnp3_master.preflight `
  --pics .\config\ems.local.json `
  --points .\config\points.local.csv `
  --plan .\config\ems_test_plan.local.json `
  --capability-matrix .\config\capability_matrix.csv `
  --json | Set-Content -Encoding utf8 .\evidence\local\preflight.json
```

报告含范围声明、设备身份、四个输入的绝对路径/大小/SHA-256、启用场景、逐能力判定、blocker 和 warning。格式见 `schemas/preflight-report.schema.json`。

报告可能包含内部路径和设备身份，外发前仍需人工脱敏。不要编辑 JSON 报告来消除 blocker；应修正原始 PICS、点表或场景计划后重新生成。

## 推荐执行顺序

```text
包 SHA-256/逐文件清单验证
  -> 包内 loopback self-test
  -> 私有配置 preflight（必须退出 0）
  -> pytest --collect-only
  -> 获批的只读 EMS 测试
  -> 外部触发的 unsolicited 测试
  -> 单场景、人工监护的控制闭环
```

任何真实状态改变仍必须满足安全手册和项目审批；预检退出 0 不会自动解锁控制。
