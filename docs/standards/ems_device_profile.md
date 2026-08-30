# EMS Device Profile / PICS 接入说明

> 状态：`PARTIAL_OPERATION_CONVENTION / FORMAL_PICS_MISSING`。项目负责人已提供来自
> `AutoExistStation_1_AutoExistStation_20260829103730.xlsx` 的“DNP3 操作约定”文本，
> 但尚无原始文件、哈希、厂商/固件身份、正式 XML Device Profile/PICS、点表和批准人。
> 因此能力矩阵中的 DUT 状态继续保持 `UNKNOWN`，不得把本页当成真实控制授权。

## 已取得的部分约定

当前文本声明 TCP 点对点通信，并给出以下固定对象选型：

| 业务 | 静态/状态对象 | 事件/命令对象 | 文档声明的 Class |
|---|---|---|---|
| 遥信 BI | G1V2 | G2V2 | Static=0，Event=1 |
| 遥测 AI | G30V5 | G32V7 | Static=0，Event=2 |
| 遥控 BO | G10V2 | G12V1 | Static=0，Command=None |
| 遥调 AO | G40V3 | G41V3 | Static=0，Command=None |
| 遥脉 | 声明“同遥测” | 声明“同遥测” | 未独立定义 |

文本还声明 G60V1～V4 Class Read、FC5/FC6 控制、8/16/32-bit range/index
限定符和 unsolicited 事件上送。上述内容是待验证的 DUT 约定，不等同于 IEEE 一致性结论。

## 与 IEEE 1815-2012 的已知差异/歧义

| 编号 | 当前约定 | 标准核对结果 | 项目处理 |
|---|---|---|---|
| D01 | FC6 `DIRECT_OPERATE_NR` 行仍列 FC129 响应 | FC6 不应返回应用响应 | 视为文档冲突；保持后端 `UNSUPPORTED_BY_BACKEND`，不得等待/伪造响应 |
| D02 | 事件 Read 请求列 06、17/28/39 | 标准事件请求使用 06/07/08；17/28 是常见事件响应 index 形式，39 为不推荐的 32-bit 形式 | 未澄清前不从文本生成可执行事件轮询断言 |
| D03 | Class 1～3 或事件 Read 总是返回空，仅 unsolicited 提供事件 | 有缓存匹配事件时 solicited Read 应返回；无事件时才是空响应 | 记录为 DUT 私有偏差候选，真实只读测试必须分别验证 |
| D04 | 支持 FC5/FC6，但未列 FC3 Select、FC4 Operate | 支持 Direct Operate 的实现还应支持 Select-Operate | SBO 状态保持 `UNKNOWN`，控制测试前必须向厂商确认 |
| D05 | 名称为 Activation Model，但 LATCH_ON/OFF 被定义为布尔保持开/关 | 该语义更接近 complementary latch；Activation Model 中两者可能都是有限时长激活动作 | 未确认点模型前禁止真实 CROB |
| D06 | 因 TCP 点对点而完全不支持广播/IIN1.0 | 这是设备/部署限制，不是 IEEE IP 配置的一般规则；FC6 还涉及标准广播要求 | LINK.BROADCAST 和 CHANNEL.UDP 保持 `UNKNOWN`，不以 TCP 推导不支持 |
| D07 | BI 固定 Class 1、AI 固定 Class 2 | Class 是逐点配置，不是 G2/G32 对象固有属性 | 可作为固件配置候选，需点表和实际响应验证 |
| D08 | 遥脉完全等同 G30/G32 遥测 | 累计脉冲通常可能使用 G20/G22 或冻结计数器；“五遥”不是 IEEE 对象分类 | 未确认回卷/冻结/计数语义前保持 Counter 与 Analog 能力 `UNKNOWN` |
| D09 | 普遍使用 02/09/39 四字节限定符 | 编码值有效但标准建议仅在确有超过 65536 点等必要时使用；OpenDNP3 3.1.2 公共 API 无法表达这三种限定符 | 优先验证 8/16-bit 首选限定符；需要 32-bit 的场景保持 `UNSUPPORTED_BY_BACKEND`，不得静默降级 |

G60 仅出现在请求中；响应应携带 G1/G2/G10/G30/G32/G40 等具体对象，而不是返回 G60。
框架已实现 FC20/FC21 显式启停和有界持续接收，但 DUT 输入仍缺启动空报文、Confirm、重发、序号、缓存和溢出策略；这些缺口不能由框架默认值代替厂商声明。

## 录入规则

- 只有取得与实际固件匹配的正式 Profile/PICS 或由 DUT 负责人逐项书面批准后，才把
  `config/ems.local.json` 的能力改成 `SUPPORTED`/`NOT_SUPPORTED`。
- 文档内部冲突一律保持 `UNKNOWN`，不得选择对测试最方便的解释。
- 真实 EMS 第一次接入只做 TCP、G60V1 和固定静态对象的只读验证；事件与控制另行解锁。
- FC6、广播、CROB 点模型和“遥脉”映射在厂商答复前都是停止条件。

## 需要提供的信息

- 厂商、型号、固件版本、配置版本和文档版本。
- DNP3 Device Profile/PICS 原始文件及 SHA-256。
- 承载类型、端点、主从链路地址、最大收发分片和超时。
- 每个功能码、对象/变体、限定词、Class、主动上送、时间同步和安全能力的声明。
- 点表：类型、索引、Class、量程、缩放、质量策略、可写性和安全影响。
- 启动总召、主动上送、事件缓存、时间同步、重连和设备重启策略。
- 实验环境标识及只读、状态改变、重启、文件删除、配置激活等授权范围。

还应要求厂商对 D01～D09 逐项书面答复，并说明设备声明的 DNP3-L1/L2/L3/L4 等级；
若不声明等级，也应提供完整的能力/限制表。

## 机器可读状态约定

导入后的每个能力只允许以下 DUT 状态：

- `SUPPORTED`
- `NOT_SUPPORTED`
- `UNKNOWN`

`UNKNOWN` 不会被自动解释为支持。正向用例应被阻塞或按测试策略标为 xfail；不支持行为测试也必须有正式条款和 DUT 声明作为依据。

## 敏感信息

证书私钥、口令、更新密钥、会话密钥及其他认证材料不得写入本文件、能力矩阵、日志或测试证据。配置中只允许保存受控密钥引用 ID。
