# 工程架构基线（0.2.0）

## 可移植边界

自动化框架只依赖 `dnp3_master` Python 公开 API，不依赖 C++ 或 OpenDNP3 类型。Python 通过严格、单会话、单在途请求的 NDJSON 协议管理 `dnp3-master-host.exe`；原生 host 通过 `IMasterBackend` 隔离固定 OpenDNP3 3.1.2 与未来扩展后端。

```text
pytest/业务断言
  -> dnp3_master client + pytest plugin
  -> stdin/stdout NDJSON v1
  -> HostController
  -> IMasterBackend
  -> OpenDnp3Backend
       -> TCP Channel + Master
       -> OpenDnp3ReadSupport
       -> OpenDnp3CommandSupport
  -> EMS Outstation
```

## 目录职责

| 目录 | 职责 | 0.2.0 状态 |
|---|---|---|
| `native/` | C++17 host、后端和原生测试 | TCP、Read/IIN、控制、安全联锁、stats 已接入 |
| `python/` | 可嵌入 pytest 的包与测试 | 类型模型、进程所有权、PICS/风险门禁和 fixtures |
| `config/` | 能力台账和 EMS Profile 示例 | 389 条能力；真实 EMS 私有配置不提交 |
| `schemas/` | NDJSON 和 EMS Profile 格式 | v1 严格 Schema |
| `scripts/` | 工具链发现、构建、测试、自检、打包 | Debug/Release/ASan 和离线校验 |
| `third_party/` | 固定源码、归档和锁 | OpenDNP3 3.1.2 及依赖已固定 |
| `docs/standards/` | 标准、PICS、依赖与证据状态 | 缺失项显式保留 |

## 生命周期与并发模型

- 一个 Python client 拥有一个 host 子进程；Windows 下 Job Object 保证父进程消失时回收整个子进程树。
- 一个 host 最多创建一个 Manager、一个 TCP Client Channel 和一个 Master。
- Python 用可重入锁保证同一时刻只有一个 NDJSON 请求在途；Read/命令支持层也拒绝重叠协议任务。
- 断开顺序为：停止/取消命令和 Read -> 取走共享资源 -> Master Disable/Shutdown -> Channel Shutdown -> Manager Shutdown -> 清除安全令牌。
- OpenDNP3 回调只写入有界的任务状态/测量结构并立即返回；不会等待 Python 或 stdout。
- stdout 由主协议线程独占，只输出 JSON；诊断写 stderr。

## Read 数据路径

`integrity_poll`、`class_poll` 和 `read` 为一次性同步任务。每个任务拥有独立 task ID、deadline、IIN 起始序号、接收序号、分片列表和有界 MeasurementStore。

detail 模式保留并返回逐点结果；summary 模式只累计计数，不保留或返回逐点记录。超过 `max_measurements` 时返回 `QUEUE_OVERFLOW`，不会静默丢数据。任务结果同时包含原始/解析 IIN、OpenDNP3 task completion、开始/完成时间和分片摘要。

当前 master 的自动启动完整性、event scan 和 unsolicited class mask 均关闭，避免建立连接时产生不可控后台任务；调用方必须显式发起 Read。持久 unsolicited 收集属于后续 T12。

## 控制数据路径与安全门

Python 仅公开 `CrobCommand` 和四种严格类型的 `AnalogOutputCommand`。命令数组最多 256 点，同类型/同索引重复在进入后端前被拒绝。结果按原请求关联每个点，保留 CommandPointState 和完整 CommandStatus，不用单个布尔值覆盖部分失败。

状态改变需通过两层门：

1. pytest 收集阶段要求用例同时带 `dnp3_dut`、能力 ID 和 `dnp3_state_changing`，并得到显式命令行/环境授权及 operator/DUT ID；未标记用例即使整次运行带了解锁参数也只能得到只读连接。
2. `connect` 只有在 `environment=LAB`、`allow_state_change=true` 和两个 ID 均有效时才生成 128-bit 会话令牌；后续每条命令必须携带正确令牌。

Python 客户端不公开令牌属性，只在内存中自动附加；高层连接结果和诊断会移除/过滤令牌，断开或进程退出后销毁。该机制只防误操作，不是认证/授权/SAv5。控制任务超时返回“执行可能不确定”，不会自动重试。

## 能力与证据模型

`config/capability_matrix.csv` 是能力状态唯一台账。`hello.capabilities` 只暴露当前实现的子集，并带实现 revision 和本机验证范围。当前本机端到端从站也使用 OpenDNP3 3.1.2，因此相关能力保持 `IMPLEMENTED_UNVERIFIED`；独立端/真实 EMS 证据齐全后才能升级。

构建时 `build-info.json` 固定 host 版本、Git commit、工作区 clean/dirty/unavailable 状态、OpenDNP3 commit、构建配置、目标架构、依赖锁哈希和能力矩阵哈希。正式证据应使用 `git_worktree_state=clean` 的构建并保存该文件，不应只记录 EXE 文件名。

## 后续扩展顺序

T12 以后按独立任务增加 unsolicited、时间同步、Restart/Freeze/Assign Class、持续 capture/性能、其他承载、经典对象缺口、高级事务和安全功能。不得扩大单会话/单在途 RPC 边界，除非有独立设计与迁移任务。

具体阻塞、输入和验收见 `docs/INTRANET_HANDOFF_REMAINING_TASKS.md`。
