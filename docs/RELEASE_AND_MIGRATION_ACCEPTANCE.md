# 干净版本发布与 pytest 迁移验收

本文定义 0.6.1 的可重复发布门禁和迁移验收边界。全部自动检查只使用本机文件与
`127.0.0.1` 测试从站，不读取私有 PICS/点表，不连接真实 EMS/DUT，也不形成 IEEE
一致性或独立互操作结论。

## 1. 正式发布只使用一键入口

先把需要发布的代码提交，并确认仓库根目录的 `git status --short` 无输出，然后执行：

```powershell
.\scripts\release.ps1 -LifecycleIterations 1000
```

需要同时验证多个已经安装好的 Python/pytest 环境时，显式列出解释器：

```powershell
.\scripts\release.ps1 `
  -LifecycleIterations 1000 `
  -PythonExecutable @(
    'D:\Python310\python.exe',
    'D:\Python312\python.exe',
    '.\.venv\Scripts\python.exe'
  )
```

脚本按顺序执行并在任一失败处停止：

1. 要求 Git HEAD 为 40 位提交且工作区没有 tracked/untracked 变化；
2. 环境体检、Release/x64 构建，并核对 `build-info.json` 精确绑定当前
   `HEAD + clean`；
3. 原生 CTest、Python 全量回归、仓库校验器和指定次数的生命周期验收；
4. 生成包、逐文件清单、SHA-256 和解包回环自检；
5. 从同一构建连续打包两次并要求 ZIP SHA-256 完全一致；
6. 对每个解释器执行包完整性、本机回环、离线 wheel 安装和空白 pytest 消费者
   验收；
7. 再次确认 HEAD/工作区没有变化，并写出机器可读报告。

成功产物位于：

```text
out\package\ems-dnp3-pytest-0.6.1\
out\package\ems-dnp3-pytest-0.6.1.zip
out\package\ems-dnp3-pytest-0.6.1.zip.sha256
out\release\migration-compatibility-report.json
out\release\release-closure-report.json
```

脚本不会创建 Git tag、提交或推送。负责人应在审阅报告、提交和远端状态后另行决定
是否打 tag/发布，避免自动改写仓库历史。

## 2. 打包脚本的 fail-closed 行为

`package.ps1` 默认只接受：

- `windows-msvc-release`；
- 当前工作区为 clean；
- 构建记录的 commit 等于当前 HEAD；
- 构建记录为 `clean + Release + x64`。

因此，`-SkipBuild` 不能把旧提交的 EXE 与新 Python 源码拼成正式包。开发者若只是
检查未提交代码的包结构，可以显式使用 `-AllowNonCleanBuild`；该开关生成的产物
不得发布、不得作为正式测试证据。正式闭环始终使用 `release.ps1`，不使用这个开关。

## 3. 为什么发布包包含 wheel

发布包内同时保留可审阅源码 `python\` 和可安装文件：

```text
python-dist\dnp3_master_test_framework-0.6.1-py3-none-any.whl
```

直接从受清单保护的源码目录执行 editable/source build，pip 可能生成 `build\` 或
`*.egg-info`，使 `package-manifest.json` 立即失效。目标 pytest 环境应从 wheel 安装：

```powershell
.\.venv\Scripts\python.exe -m pip install `
  --no-index `
  --no-deps `
  '.\third_party\ems_dnp3\python-dist\dnp3_master_test_framework-0.6.1-py3-none-any.whl'
```

核心包没有第三方运行时依赖；pytest 8.x/9.x 仍应由目标框架自己的离线软件源或
批准 wheel 提供。

## 4. 在目标框架执行迁移验收

把完整发布包放到 `third_party\ems_dnp3` 后，在目标 pytest 项目根目录执行：

```powershell
& '.\third_party\ems_dnp3\compatibility-test.ps1' `
  -PythonExecutable '.\.venv\Scripts\python.exe' `
  -ReportPath '.\artifacts\dnp3-migration-compatibility.json'
```

该脚本会：

- 严格验证 `package-manifest.json`；
- 使用包内 host/outstation 完成回环读、控制反馈和 8 点 capture；
- 用 `pip --target --no-index --no-deps` 把包内 wheel 安装到系统临时目录，不修改
  目标虚拟环境或发布包；
- 清空调用者的 `DNP3_*` 配置、禁用第三方 pytest 自动加载，在临时空白项目中手工
  启用 `dnp3_master.pytest_plugin`；
- 断言导入路径确实位于隔离安装目录、Python/host/manifest 版本一致、插件参数和
  安全默认值可用；
- 清理临时目录并恢复原进程环境。

只有报告中的 `overall_passed=true` 且每个解释器的 `status=PASS` 才算迁移验收
通过。报告中的绝对路径属于本机诊断信息，外发前仍需人工检查。

## 5. 兼容性声明边界

脚本接受一个或多个 Python 3.10+ 解释器，并要求其中已安装 pytest 8.x 或 9.x。
它只报告实际执行过的组合；没有安装的 Python/pytest 组合保持“未覆盖”，不能由
单个较新解释器的通过结果推断全部版本均已验证。PowerShell 脚本应分别在 Windows
PowerShell 5.1 和 PowerShell 7 下执行或至少解析；正式目标机仍需运行自己的迁移
报告。

此验收证明的是包完整性、Windows 本机运行链和 pytest 接入方式。真实 EMS 的 IP、
PICS、点表、主动上报、控制和性能/24 小时结论继续按内网任务卡单独验证。
