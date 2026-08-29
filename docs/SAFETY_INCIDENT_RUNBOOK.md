# 不确定控制结果事故锁处理手册

本手册处理一种高风险情况：控制请求已经发出，但主站无法确定 EMS 是否执行。典型原因包括 DNP3 响应超时、host 返回 `execution_uncertain=true` / `may_still_execute=true`、Python 等待 host 超时/退出、控制结果结构损坏，或“成功响应”自身仍标记为不确定。

## 框架会自动做什么

发生上述任一情况后，Python 客户端按顺序执行：

1. 在持久目录的 `active/` 下写入 DUT 专属事故锁；
2. 清除内存中的安全令牌；
3. 立即销毁当前 host 会话，禁止复用；
4. 在原异常中附加 `incident_id`、`persistent_safety_lock=true` 和所需动作；
5. 后续进程仍可建立只读连接，但任何控制在发包前都会被 `UnresolvedSafetyIncidentError` 拦截。

事故文件只保存 DUT 身份 SHA-256、命令类型/点号、命令载荷 SHA-256、错误类型和时间，不保存 DUT ID 明文、控制值、安全令牌或读回原始内容。格式见 `schemas/safety-incident.schema.json`。

如果事故锁无法可靠写入，框架仍会销毁 host，并抛出 `SafetyIncidentPersistenceError`。此时不能把“没有锁文件”解释为安全，必须停止全部控制、人工读回并修复事故目录。

## 正确处置步骤

1. 记录异常中的 `incident_id`，停止自动重试和后续控制。
2. 用新的客户端进程连接同一 DUT，只执行只读查询；也可使用经过批准且独立于本控制链路的 EMS/HMI 状态源。
3. 按批准点表读取对应输出状态/反馈点，并检查相关联锁、遥信和设备实际状态。不能用“命令已发送”代替读回。
4. 把读回结果保存到受控的本地证据文件，记录证据引用、确认人和简短结论。
5. 显式确认准确的 `incident_id`。框架只保存读回 JSON 的 SHA-256，随后把事故记录移入 `archive/`。
6. 再次确认 `active_safety_incident()` 返回 `None`，之后才能由负责人决定是否发起一条全新的控制。

禁止直接删除、改名或编辑 `active/*.json`。锁文件损坏时框架会保持锁定；应保留原文件，修复存储后由负责人处置。

## Python 处置示例

直接使用客户端时，必须配置持久目录：

```python
from pathlib import Path
from dnp3_master import Dnp3MasterClient, HostProcessConfig

client = Dnp3MasterClient(
    HostProcessConfig(
        executable=Path("bin/dnp3-master-host.exe"),
        safety_incident_directory=Path("evidence/local/safety-incidents"),
    )
)
```

不确定结果发生后，新建客户端、使用完全相同的 `dut_id` 连接并完成只读读回，然后：

```python
incident = new_client.active_safety_incident()
assert incident is not None

new_client.acknowledge_safety_incident(
    incident["incident_id"],
    acknowledged_by="approved-reviewer-or-ticket",
    readback_summary="G10V2 feedback and independent device state agree",
    readback={
        "group": 10,
        "variation": 2,
        "index": 7,
        "observed_value": False,
        "source": "approved-static-read",
    },
    evidence_reference="evidence/run-20260829/readback-control-007.json",
)
```

pytest 插件默认使用当前项目下的 `evidence/local/safety-incidents`；可用 `--dnp3-safety-incident-dir` 或 `DNP3_SAFETY_INCIDENT_DIR` 指向获批的持久位置。

## 无 pytest 时的命令行处置

先查询，不改变锁：

```powershell
python -m dnp3_master.safety_incident_cli `
  --directory ".\evidence\local\safety-incidents" `
  status --dut-id "lab-ems-asset-id"
```

确认前把独立读回内容写入未提交的 JSON 文件，然后执行：

```powershell
python -m dnp3_master.safety_incident_cli `
  --directory ".\evidence\local\safety-incidents" `
  acknowledge `
  --dut-id "lab-ems-asset-id" `
  --incident-id "INC-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
  --acknowledged-by "approved-reviewer-or-ticket" `
  --readback-summary "approved independent readback completed" `
  --readback-json-file ".\evidence\local\readback.json" `
  --evidence-reference "evidence/local/readback.json"
```

## 必须知道的边界

- 锁以 `dut_id` 的哈希为键；同一设备每次必须使用完全相同的资产 ID，否则会被视为另一个 DUT。
- 跨 Python 进程有效，但跨机器只有在这些机器使用同一个经过批准、具备可靠原子文件语义的共享事故目录时才有效。多机控制应由外部调度器保证单控制器所有权。
- 事故锁不能消除两个控制器在锁创建前同时发命令的竞态。真实控制测试必须串行，禁止 pytest-xdist 多 worker 或多个主站并行控制同一 DUT。
- “确认并归档”只解除技术联锁，不代表原控制成功，也不替代工单批准、双人复核、回退方案或设备安全程序。
- 本地回归覆盖新进程拦截、只读可用、错误 ID 拒绝、读回哈希、损坏锁 fail-closed、归档中断恢复、Python-host 超时和不确定成功结果；真实 EMS 行为仍须内网联调验证。
