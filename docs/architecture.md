# 工程架构基线

## 可移植边界

自动化框架只依赖 `dnp3_master` Python 包公开 API，不依赖 C++ 类型或 OpenDNP3 类型。Python 通过单会话、单在途请求的 NDJSON 协议管理 `dnp3-master-host.exe`。原生 host 通过后端接口隔离首选的 OpenDNP3 3.1.2 与未来扩展/替换后端。

## 当前 T05 组成

| 目录 | 职责 | 当前状态 |
|---|---|---|
| `native/` | C++17 host 及原生单测 | 严格 NDJSON；可注入后端边界；OpenDNP3 TCP client 生命周期和有界状态事件 |
| `python/` | pytest 面向的包与测试 | 子进程所有权、TCP 配置/API、超时、诊断、Job Object 和连接 fixtures |
| `config/` | 能力目录与后续 DUT 配置 | 389 条受门禁保护的目录项 |
| `scripts/` | Windows 环境发现、构建和统一测试 | 可用 |
| `third_party/` | 固定源码、归档和离线依赖锁 | OpenDNP3 3.1.2 及三项传递依赖已锁定 |
| `docs/standards/` | 标准、PICS 和依赖输入状态 | 缺失项显式记录 |

## 已完成与后续演进

- T02 已完成：严格 NDJSON、`hello`、`get_status`、`shutdown`、Schema 和输入限制。
- T03 已完成：Python 子进程管理、超时、stdout/stderr 排空、Windows Job Object、fixtures 和生命周期压力门禁。
- T04 已完成：OpenDNP3 3.1.2 的离线传递依赖、许可证汇总、构建身份和运行时链接冒烟门禁。
- T05 已完成：建立首个真实后端，交付 TCP channel connect/disconnect、连接超时清理、自动重连与状态事件。
- T06：在不改变后端边界的前提下交付总召/Class Read 与任务完成模型。

`IMasterBackend` 只暴露已实现的连接生命周期与事件操作；Read、Operate 等后续命令仍返回 `UNSUPPORTED_BY_BACKEND`，不会用空结果制造假成功。OpenDNP3 回调只向容量固定的队列复制状态并立即返回，stdout 仍由主协议线程独占。
