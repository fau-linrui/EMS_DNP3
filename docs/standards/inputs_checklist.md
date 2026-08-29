# T00 项目输入检查清单

检查日期：2026-08-28  
目标版本：IEEE 1815-2012 / OpenDNP3 3.1.2 / Windows x64  
状态定义：`PRESENT`、`MISSING`、`REVIEW_REQUIRED`。

本清单只记录当前仓库中可验证的事实。`MISSING` 项不会用公开摘要、博客或推测内容补齐。

| 输入 | 状态 | 当前证据/缺口 | 建议负责人 | 阻塞范围 |
|---|---|---|---|---|
| 项目开发指导书 | PRESENT | 仓库根目录 `DNP3_Windows_Master_Automation_Development_Guide_IEEE1815-2012.md` | PROJECT_OWNER | 无 |
| IEEE 1815-2012 正式文本的合法内部访问位置 | REVIEW_REQUIRED | 本机已有疑似完整 IEEE Std 1815-2012 PDF，SHA-256 已登记；结构检查通过，但文件带第三方机构许可水印，来源/授权范围和勘误仍待负责人确认；PDF 已排除出 Git | STANDARDS_OWNER | 可用于本地条款复核准备；对外发布、正式合规声明及最终引用仍阻塞 |
| 与 2012 版匹配的勘误/技术公告清单 | MISSING | 未提供 | STANDARDS_OWNER | 标准解释与一致性断言 |
| EMS Device Profile/PICS（含版本与签名/哈希） | REVIEW_REQUIRED | 已提供来自 `AutoExistStation_1_AutoExistStation_20260829103730.xlsx` 的“DNP3 操作约定”文本，但无原始文件/哈希、厂商、固件和批准信息；事件 Read、FC6 响应、SBO、CROB 点模型、广播和遥脉映射存在待澄清项，详见 `ems_device_profile.md` | DUT_OWNER | 可用于缩小询问范围；不能驱动最终 DUT 断言或状态改变测试 |
| EMS 点表 | MISSING | 未提供测点类型、索引、Class、量程及可写属性 | DUT_OWNER | 真实 EMS 功能与性能用例 |
| EMS 连接参数 | MISSING | 未提供承载、地址、端口、链路地址、超时、最大分片 | DUT_OWNER | 连接与互操作测试 |
| EMS 主动上送和启动策略 | REVIEW_REQUIRED | 操作约定声明事件依靠 unsolicited，但未提供 FC20/FC21、启动空响应、Confirm、重发、序号、缓存和溢出参数，并与 Class/Event Read 规则存在歧义 | DUT_OWNER | unsolicited 与启动时序测试 |
| EMS 安全能力与授权边界 | MISSING | 框架已实现本地 fail-closed 控制联锁，但未提供真实实验环境标识、书面操作授权或 SAv5 声明 | SECURITY_OWNER | 真实控制、重启、时间、文件、配置及 SAv5 测试 |
| OpenDNP3 3.1.2 固定源码 | PRESENT | 官方 tag `3.1.2` 已下载到 `third_party/opendnp3`；commit `26b4c01e4839bbbda8866655e086471c4917ee53`；官方 ZIP SHA-256 `7cb1a8a84f95c05b579a48543687a78c7dc9e3c394883419a92355a4aa6c1d5f` | DEPENDENCY_OWNER | 主源码不再阻塞 |
| OpenDNP3 离线构建依赖 | PRESENT | Asio `asio-1-16-0`、exe4cpp `fb878a4...`、ser4cpp `3c449734...` 的原始归档、SHA-1/SHA-256、源码树 SHA-256 和许可证由 `third_party/opendnp3-dependencies.lock.json` 固定；CMake 配置时强制复核且禁止网络回退 | DEPENDENCY_OWNER | 不阻塞 OpenDNP3 构建；功能/API 结论仍需逐项复核 |
| OpenDNP3 许可证副本 | PRESENT | Apache-2.0 `LICENSE` 与 `NOTICE` 已复制到 `LICENSES/opendnp3/` 并记录 SHA-256 | DEPENDENCY_OWNER | 不阻塞源码审查；发布时仍需统一 NOTICE |
| nlohmann/json 固定源码与许可证 | PRESENT | 官方 v3.12.0 单头文件已离线固定；commit、发布文件 SHA-256 与 MIT 许可证记录于 `third_party/nlohmann_json.lock.json` | DEPENDENCY_OWNER | 不阻塞 T02；发布前仍需统一第三方 NOTICE |
| Visual Studio 2022 C++ 工具链 | PRESENT | Build Tools 17.14.39；MSVC x64 19.44.35228；MSBuild 17.14.51；Windows SDK 10.0.26100.0，已从 Developer PowerShell 实际验证 | BUILD_OWNER | 不阻塞 T01 |
| CMake | PRESENT | Visual Studio 随附 CMake/CTest 3.31.6 与 Ninja 1.12.1；`Visual Studio 17 2022` 生成器可用 | BUILD_OWNER | 不阻塞 T01 |
| Python | PRESENT | 当前环境为 Python 3.14.6；项目声明最低 Python 3.10 | BUILD_OWNER | 更低/更高版本仍需按内网目标解释器验证 |
| pytest | PRESENT | 项目本地 `.venv` 已安装 pytest 9.1.1；`pyproject.toml` 固定兼容 pytest >=8,<10 | BUILD_OWNER | 内网需准备 pytest wheel/镜像或复用已有环境 |
| 独立参考从站 | MISSING | 未提供产品、版本、配置和实验室端点 | INTEROP_OWNER | `VERIFIED_INTEROP` 证据 |
| 非 OpenDNP3 独立实现/一致性工具 | MISSING | 未提供 | INTEROP_OWNER | 独立互操作与一致性证据 |
| PCAP/报告证据存储位置与保留策略 | MISSING | 未提供 | QA_OWNER | 可归档运行与证据哈希 |
| GitHub 目标仓库 | PRESENT | 已配置 `git@github.com:fau-linrui/EMS_DNP3.git`；根目录已初始化 `main` 分支并完成首次提交；标准 PDF、构建产物、虚拟环境和本地配置均由忽略规则排除 | PROJECT_OWNER | 无 |

## 当前结论

- T00～T11 的通用工程、TCP、核心 Read/IIN 和有响应控制路径已实现；本机回环与同栈集成测试通过。
- 本机标准 PDF 的版本与结构已确认，T05～T11 所需条款和对象表已建立单次本地技术索引；来源/授权、勘误和全目录双人复核仍未完成，因此不能据此形成对外合规声明。
- OpenDNP3 3.1.2 主源码及传递构建依赖已经固定；已对当前 TCP/Read/Control 公共 API 做定向复核。其余能力仍需按 PICS 逐项复核，不能把同栈本机测试提升为独立互操作。
- 已取得部分 EMS 操作约定，但它不是与固件绑定的正式 PICS，且存在 D01～D09 待确认项；`dut_pics_status` 继续保持 `UNKNOWN`。
- 在收到安全授权前，真实 EMS 只运行 PICS 允许的只读测试；状态改变用例在 pytest 收集和 host 会话两层均默认锁定。

## 解除阻塞所需最小交付物

1. 确认 `standard_access.md` 中 PDF 的来源/使用授权、实名负责人及匹配的勘误版本，并对当前单次条款索引做第二人复核。
2. 提供 EMS Device Profile/PICS 原件和哈希，答复 `ems_device_profile.md` 的 D01～D09，并提供点表、实验室连接参数和状态改变授权（敏感值只通过本地未提交配置注入）。
3. 指定非同栈独立参考端、抓包位置、证据保留规则和性能阈值。
