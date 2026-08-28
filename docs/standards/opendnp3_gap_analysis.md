# OpenDNP3 3.1.2 缺口分析

> 状态：`IN_PROGRESS`。固定版 OpenDNP3 源码、commit、源码包、三项离线构建依赖、摘要和许可证已经进入项目。T05 已完成 TCP client 生命周期的源码/API 复核与本机行为测试；其余公共 API 与功能缺口复核尚未完成。

## 固定依赖记录

| 字段 | 当前值 |
|---|---|
| 目标版本 | 3.1.2 |
| 源码位置 | `third_party/opendnp3/` |
| 完整 commit | `26b4c01e4839bbbda8866655e086471c4917ee53` |
| tag / tag object | `3.1.2` / `523f7d2d6c65aa671f594fc63c3adcd74e81407d` |
| Git tree | `18fedf6a7cf5aa98f7556afafec848d7e61f8f24` |
| 源码包 | `third_party/distfiles/opendnp3-3.1.2.zip` |
| 源码包 SHA-256 | `7cb1a8a84f95c05b579a48543687a78c7dc9e3c394883419a92355a4aa6c1d5f` |
| 许可证文件 | `LICENSES/opendnp3/LICENSE` 和 `LICENSES/opendnp3/NOTICE` |
| 构建依赖锁 | `third_party/opendnp3-dependencies.lock.json` |
| 离线依赖 | Asio `asio-1-16-0`；exe4cpp `fb878a4...`；ser4cpp `3c449734...` |
| 运行时冒烟 | `native/tests/opendnp3_runtime_tests.cpp` |
| 官方 master example 路径 | `third_party/opendnp3/cpp/examples/master/` |
| 自动校验日期 | 2026-08-28 |
| 人工源码/API 复核人 | MISSING |

## 复核方法

每个能力必须分别回答以下五个问题，并引用固定源码的文件与符号：

1. 编解码器能否表达该对象和限定词？
2. Master 公共 API 能否发起该事务？
3. 回调能否无损交付值、flags、时间、IIN 和状态？
4. 是否存在完整事务状态机和资源清理？
5. 是否已有独立互操作证据？

仅存在枚举或解析类型不能判定端到端支持。

## 待源码复核的初始缺口表

| 能力族 | 指导书初始判断 | 必须读取的固定源码证据 | 当前结论 |
|---|---|---|---|
| 常用 BI/DBBI/BOS/Counter/Analog/AOS 静态与事件 | 大部分支持 | `cpp/examples/master`、Master API headers、SOE handler overloads | BLOCKED |
| Class 0/1/2/3 扫描和主动上送 | 支持 | Scan APIs、task lifecycle、unsolicited handling | BLOCKED |
| CROB/Analog Output 的 SBO 与 Direct Operate | 支持；No Response 需单独确认 | `ICommandProcessor`、`CommandSet`、结果回调 | BLOCKED |
| TCP client | 通道 API 支持 | `DNP3Manager::AddTCPClient`、`IChannel::AddMaster`、`IMaster::Enable/Disable/Shutdown`、`IChannelListener::OnStateChange`；`native/src/OpenDnp3Backend.cpp`；`python/tests/test_host_tcp.py` | IMPLEMENTED_UNVERIFIED；本机 TCP connect/FIN reconnect/disconnect/timeout/shutdown 已测，独立 DNP3 互操作仍缺失 |
| TCP server / TLS / UDP / Serial | 通道 API 存在，框架尚未接入 | `DNP3Manager` 与 channel headers/examples | BLOCKED |
| Group 0 Device Attributes | 部分/缺失 | object definitions、decoder、Master API delivery | BLOCKED |
| Group 31/33 Frozen Analog | 公共支持不足 | generated objects、decoder、SOE overloads | BLOCKED |
| Group 34 Deadband | 缺失 | object catalog、write/read header support | BLOCKED |
| Group 13/43 Command Event | 部分/缺失 | object catalog、SOE delivery | BLOCKED |
| Group 50V1/V2 与 Group 80 主站读取 | 限制/缺口 | Header factories、read path、callbacks | BLOCKED |
| Group 110/111 Octet String | 支持 | length variants、SOE delivery、limits | BLOCKED |
| 广播与 self-address | 部分/缺失 | link-layer addressing and public configuration | BLOCKED |
| File Transfer / Group 70 | 无完整业务实现 | object support plus transaction API/state machine | BLOCKED |
| Data Set / Groups 83/85-88 | 未实现 | object catalog and public API | BLOCKED |
| Virtual Terminal / Groups 112-113 | 未实现 | object catalog and public API | BLOCKED |
| SAv5 / Groups 120-122 | 未实现 | object catalog/security modules/public API | BLOCKED |

## 完成此分析所需输出

- 每行补充固定 commit 下的源码路径、类型/函数名和测试路径。
- 分开记录 `codec_status`、`master_api_status`、`callback_status`、`transaction_status` 和 `interop_status`。
- 将明确缺失项映射到 `capability_matrix.csv`，使用 `UNSUPPORTED_BY_BACKEND`；不能返回空成功。
- 形成后端选型评审后，才能决定内部 fork、扩展后端或替换协议栈。
