# 内网 EMS 只读点表测试模板

该目录可以整体复制进已有 pytest 工程。测试只执行 DNP3 Read，不包含遥控或遥调命令。

1. 安装或复制 `dnp3_master` Python 包，并准备已构建的 `dnp3-master-host.exe`。
2. 将仓库的 `config/points.example.csv` 复制为私有的 `points.local.csv`，替换点号、类型和对象变体；不要把真实点表提交到公开 Git 仓库。
3. 准备私有 PICS JSON。没有 PICS 时，DUT 测试默认不会正常执行。
4. 配置 `DNP3_MASTER_HOST_EXE`、`DNP3_OUTSTATION_HOST`、`DNP3_PICS_FILE`、`DNP3_POINTS_FILE` 等环境变量。
5. 在该目录执行 `pytest -ra`。

点表加载器会在建立 DUT 连接前拒绝错误列名、重复点、越界索引、对象组与点类型不匹配、事件字段填写不完整以及非有限期望值。把某行的 `enabled` 设为 `false` 可临时停用该点。
