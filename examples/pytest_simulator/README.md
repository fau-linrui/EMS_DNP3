# 模拟 EMS 入门套件

完整步骤见 [单配置入门指南](../../docs/SIMULATOR_QUICKSTART.md)。
复制 `settings.example.json` 为 `settings.local.json`，填 EMS IP/监听端口、DNP3
链路地址和点号。使用已安装 `dnp3_master` 和 pytest 的 Python，在此目录执行：

```powershell
python -m pytest -k test_00 -v
python -m pytest -v -s
```

第二条执行配置中的遥控遥调并等待外部 BI/AI 变化。SIMULATOR 不需要审批参数，
不要用于真实设备。无时间同步、Restart 或信号生成。私有配置不提交、不打包；
移植时保留本目录全部文件，并填写配套运行包的 `runtime_root`。

自动定位只是寻找并校验 `dnp3-master-host.exe`，不会自动下载或编译；GitHub 源码需先
构建，运行包需完整解压。复制本目录到其他工程后，上面的相对文档链接可能不再适用，
请阅读运行包内的 `docs/SIMULATOR_QUICKSTART.md`。
