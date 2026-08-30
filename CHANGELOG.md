# 版本变更记录

本文件记录用户可见行为和移植边界的变化。能力是否适用于某台 EMS，仍以该设备的正式 PICS、私有配置和不可变测试证据为准；版本记录不能替代互操作或一致性结论。

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
