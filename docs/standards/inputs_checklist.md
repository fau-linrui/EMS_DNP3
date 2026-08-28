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
| EMS Device Profile/PICS（含版本与签名/哈希） | MISSING | 未提供 | DUT_OWNER | PICS 驱动选择、最终 DUT 断言 |
| EMS 点表 | MISSING | 未提供测点类型、索引、Class、量程及可写属性 | DUT_OWNER | 真实 EMS 功能与性能用例 |
| EMS 连接参数 | MISSING | 未提供承载、地址、端口、链路地址、超时、最大分片 | DUT_OWNER | 连接与互操作测试 |
| EMS 主动上送和启动策略 | MISSING | 未提供 | DUT_OWNER | unsolicited 与启动时序测试 |
| EMS 安全能力与授权边界 | MISSING | 未提供实验环境标识、安全操作授权或 SAv5 声明 | SECURITY_OWNER | 控制、重启、文件、配置及 SAv5 测试 |
| OpenDNP3 3.1.2 固定源码 | PRESENT | 官方 tag `3.1.2` 已下载到 `third_party/opendnp3`；commit `26b4c01e4839bbbda8866655e086471c4917ee53`；官方 ZIP SHA-256 `7cb1a8a84f95c05b579a48543687a78c7dc9e3c394883419a92355a4aa6c1d5f` | DEPENDENCY_OWNER | 主源码不再阻塞 |
| OpenDNP3 离线构建依赖 | PRESENT | Asio `asio-1-16-0`、exe4cpp `fb878a4...`、ser4cpp `3c449734...` 的原始归档、SHA-1/SHA-256、源码树 SHA-256 和许可证由 `third_party/opendnp3-dependencies.lock.json` 固定；CMake 配置时强制复核且禁止网络回退 | DEPENDENCY_OWNER | 不阻塞 OpenDNP3 构建；功能/API 结论仍需逐项复核 |
| OpenDNP3 许可证副本 | PRESENT | Apache-2.0 `LICENSE` 与 `NOTICE` 已复制到 `LICENSES/opendnp3/` 并记录 SHA-256 | DEPENDENCY_OWNER | 不阻塞源码审查；发布时仍需统一 NOTICE |
| nlohmann/json 固定源码与许可证 | PRESENT | 官方 v3.12.0 单头文件已离线固定；commit、发布文件 SHA-256 与 MIT 许可证记录于 `third_party/nlohmann_json.lock.json` | DEPENDENCY_OWNER | 不阻塞 T02；发布前仍需统一第三方 NOTICE |
| Visual Studio 2022 C++ 工具链 | PRESENT | Build Tools 17.14.39；MSVC x64 19.44.35228；MSBuild 17.14.51；Windows SDK 10.0.26100.0，已从 Developer PowerShell 实际验证 | BUILD_OWNER | 不阻塞 T01 |
| CMake | PRESENT | Visual Studio 随附 CMake/CTest 3.31.6 与 Ninja 1.12.1；`Visual Studio 17 2022` 生成器可用 | BUILD_OWNER | 不阻塞 T01 |
| Python | PRESENT | 当前环境为 Python 3.14.6；项目最低/最高支持版本尚未定版 | BUILD_OWNER | Python 包兼容性决策 |
| pytest | PRESENT | 项目本地 `.venv` 已安装 pytest 9.1.1；正式版本约束将在 T01 的 `pyproject.toml` 固定 | BUILD_OWNER | 不阻塞 T00；离线依赖锁定仍待 T01 |
| 独立参考从站 | MISSING | 未提供产品、版本、配置和实验室端点 | INTEROP_OWNER | `VERIFIED_INTEROP` 证据 |
| 非 OpenDNP3 独立实现/一致性工具 | MISSING | 未提供 | INTEROP_OWNER | 独立互操作与一致性证据 |
| PCAP/报告证据存储位置与保留策略 | MISSING | 未提供 | QA_OWNER | 可归档运行与证据哈希 |
| GitHub 目标仓库 | PRESENT | 已配置 `git@github.com:fau-linrui/EMS_DNP3.git`；根目录已初始化 `main` 分支并完成首次提交；标准 PDF、构建产物、虚拟环境和本地配置均由忽略规则排除 | PROJECT_OWNER | 无 |

## 当前结论

- T00 可以完成工程输入盘点、能力目录骨架和校验器。
- 本机标准 PDF 的版本与结构已确认，T05 所需 TCP/链路地址条款已建立本地技术索引；来源/授权、勘误和全目录双人复核仍未完成，因此不能据此形成对外合规声明。
- OpenDNP3 3.1.2 主源码及传递构建依赖已经固定，并通过 `DNP3Manager` 构造/关闭运行时冒烟测试；公共 Master API 的逐项功能缺口分析仍未完成，不能把指导书中的初始判断提升为已实现状态。
- 因缺少 EMS PICS，`dut_pics_status` 保持 `UNKNOWN`。
- 在收到安全授权前，只规划只读测试；任何改变 EMS 状态的用例默认禁止运行。

## 解除阻塞所需最小交付物

1. 确认 `standard_access.md` 中 PDF 的来源/使用授权、实名负责人及匹配的勘误版本，并建立逐条索引。
2. 提供 EMS Device Profile/PICS、点表与实验室连接参数（敏感值可通过本地未提交配置注入）。
3. 指定参考从站、抓包位置和证据保留规则。
