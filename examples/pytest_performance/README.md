# 可复制的只读性能与 soak 用例

本目录只执行 Read/Integrity/Class Poll，不发送控制命令。复制到内网 pytest
框架后，先从 `config/performance_profile.example.json` 建立未提交的
`performance_profile.local.json`，把 scope 改为
`TARGET_ENVIRONMENT_PENDING_REVIEW`，并填入批准的对象分布、点数和阈值。

首次只运行有界 benchmark：

```powershell
python -m pytest .\examples\pytest_performance\test_read_performance.py -q `
  --dnp3-host-exe .\bin\dnp3-master-host.exe `
  --dnp3-pics-file .\config\ems.local.json `
  --dnp3-capability-matrix .\config\capability_matrix.csv `
  --dnp3-performance-profile .\config\performance_profile.local.json `
  --dnp3-outstation-host '<EMS_IP>' `
  --dnp3-outstation-port <EMS_PORT> `
  --dnp3-unknown-policy error `
  --dnp3-performance-report-dir .\evidence\local\performance
```

只有 benchmark、Profile/阈值和独占环境评审通过后，才在有人监护的独占
测试机上追加 `--dnp3-run-soak`。示例 Profile 的 86400 秒即 24 小时。
Ctrl+C、host 退出、watchdog、磁盘/证据上限、连接次数或阈值失败都不会被
写成通过；中断的运行也不会与下一次运行合并。runner 会消费有界 native
channel-event 队列来发现状态快照之间的短暂断线，任何事件 drop 都 fail closed；
Profile 加载器也会拒绝不能覆盖 begin、Read、end/drain RPC 总预算的 watchdog。

报告中的 `formal_dut_conclusion` 始终为 false。正式结论还必须绑定测试机、
电源计划、网卡、独立参考端、DUT 资源、网络字节/PCAP 和评审记录；本模板
不会替你推断这些证据。
