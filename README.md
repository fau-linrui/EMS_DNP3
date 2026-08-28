# DNP3 Windows Master Automation Test Framework

面向 Windows x64、IEEE 1815-2012 和 pytest 的 DNP3 主站自动化测试框架。

当前已完成 T05：`dnp3-master-host.exe` 已通过可替换的 `IMasterBackend` 接入固定的 OpenDNP3 3.1.2，支持 TCP 主动连接、断开、连接超时清理、指数退避自动重连和有界通道状态事件。Python 包提供 `TcpConnectionConfig`、`connect()`、`disconnect()`、`wait_event()` 与 `connected_master` fixture。本阶段只验证本机 TCP 生命周期，并未实现 DNP3 Read/遥控，也不构成与独立从站或真实 EMS 的互操作结论。

## 设计边界

```text
pytest tests
    -> Python dnp3_master package
    -> stdin/stdout NDJSON
    -> dnp3-master-host.exe
    -> replaceable backend (OpenDNP3 3.1.2 first)
    -> EMS outstation
```

- `python/` 是可复制到既有 pytest 框架的测试语义层。
- `native/` 是 Windows 原生 host；协议栈实现不会泄漏到 pytest 用例。
- `config/capability_matrix.csv` 是能力状态与证据的唯一台账。
- OpenDNP3 缺失功能必须明确失败，不会以空实现或假成功代替。
- 默认只允许只读实验；控制、重启、文件和配置操作将在后续加入安全门。

## 环境

- Windows x64
- Visual Studio 2022 Build Tools，含 MSVC x64 和 Windows SDK
- CMake 3.25 或更高版本
- Python 3.10 或更高版本
- pytest 8.x 或 9.x

构建脚本通过 Visual Studio Installer 自动定位 Build Tools，不要求把 `cmake`、`cl` 或 `msbuild` 永久加入系统 PATH。

## 构建与测试

在仓库根目录执行：

```powershell
.\scripts\build.ps1 -Preset windows-msvc-release
.\scripts\test.ps1 -Preset windows-msvc-release
```

构建入口会先复核离线依赖锁、归档 SHA-1/SHA-256、源码树 SHA-256 和许可证副本；摘要不匹配时配置会直接失败。正常构建不会下载依赖。

也提供以下预设：

- `windows-msvc-debug`
- `windows-msvc-release`
- `windows-msvc-asan`

Release 原生程序生成于：

```text
out/build/windows-msvc-release/bin/dnp3-master-host.exe
out/build/windows-msvc-release/bin/build-info.json
```

该程序等待 stdin 上的 NDJSON 请求，并保证 stdout 只输出协议 JSON。例如：

```powershell
'{"schema_version":1,"id":"demo-1","cmd":"hello","params":{}}' |
    .\out\build\windows-msvc-release\bin\dnp3-master-host.exe
```

完整信封见 `docs/protocol.md`；客户端复制与 fixture 使用方式见 `docs/python_client.md`。

生命周期压力验收：

```powershell
.\scripts\test-lifecycle.ps1 -Preset windows-msvc-release -Iterations 1000
```

## 目录迁移

后续集成到现有 pytest 框架时，主要复制：

```text
python/src/dnp3_master/
config/
schemas/                 # T02 建立
bin/dnp3-master-host.exe
bin/build-info.json
dependency-locks/
```

项目状态、缺失输入与标准依据见 `docs/standards/`。本地标准 PDF 的来源授权、EMS PICS/连接参数和独立参考从站仍待提供；这些缺口继续阻止正式互操作或一致性声明。
