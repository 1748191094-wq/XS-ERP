# 多端本地部署与定期汇总

目标电脑未安装 Python 时，直接使用
`deploy/artifacts/service-windows-no-python.zip`。必须完整解压后运行
“免Python-启动主机.cmd”或“免Python-启动终端.cmd”，不能只复制 EXE；
详细步骤见包内 `README-免Python部署.txt`。

## 设计结论

每台电脑运行独立的 FastAPI 与 SQLite，断网时仍可录入。主机是权威节点：

1. 终端扫描本机客户、设备、维修工单、服务工单、报价、库存物料、库存流水和财务流水。
2. 只把发生变化的 JSON 事件推送到主机，不复制或直接合并 SQLite 文件。
3. 主机按业务编号合并，生成单调递增的变更序号。
4. 终端从上次游标继续拉取主机变更并更新本地数据库。
5. 库存流水到达主机后，主机忽略终端提交的变动前/后余额，以主机当前库存重新计算，
   再把权威余额和流水下发到各终端。
6. 财务流水按流水号幂等合并，主机和终端都会重新计算关联工单的收款、成本和利润。
7. 同一记录在两端同时修改时进入 `sync_conflicts`，禁止静默覆盖；管理员可选择保留
   主机版本或采用终端版本。

附件二进制文件不参与同步。报价 PDF、检测报告、完成报告和邮件附件应在需要时，
使用本机数据库与统一模板重新生成。

## 主机配置

主机 `.env`：

```dotenv
SERVER_HOST=0.0.0.0
ALLOW_LAN=true
SYNC_ROLE=host
SYNC_NODE_NAME=SRV-主机
SYNC_SHARED_SECRET=请使用至少24位随机字符串且每台终端保持一致
```

执行：

```powershell
python -m alembic upgrade head
python scripts\run_host.py --allow-lan
```

只允许可信局域网访问主机端口，不要把同步 API 映射到公网。

## 终端配置

每台终端必须使用独立目录和独立数据库，不得复制已经运行过的 `.env` 中的
`SYNC_NODE_ID`。第一次运行会在本机数据库生成节点 ID。

账号密码和登录会话不会同步。请在各终端建立与主机相同的用户名；同步工单会按
用户名恢复负责人关系，但不会下发密码或会话令牌。

```dotenv
SERVER_HOST=127.0.0.1
ALLOW_LAN=false
SYNC_ROLE=terminal
SYNC_NODE_NAME=前台-01
SYNC_HOST_URL=http://192.168.1.20:8000
SYNC_SHARED_SECRET=与主机一致的至少24位随机字符串
SYNC_INTERVAL_SECONDS=300
```

终端启动应用：

```powershell
python -m alembic upgrade head
python scripts\run_host.py --standalone
```

另开一个后台窗口运行同步代理：

```powershell
python scripts\run_sync_node.py --watch
```

先用一次性命令验收：

```powershell
python scripts\run_sync_node.py
```

确认一次同步成功后，安装每 5 分钟执行一次的 Windows 计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_sync_task.ps1 -IntervalMinutes 5
```

安装后可手动立即触发：

```powershell
Start-ScheduledTask -TaskName "SRV-Repair-Periodic-Sync"
```

## 冲突和恢复

- 管理员可在“多端同步”页面查看和处理冲突。
- `POST /api/sync/conflicts/{conflict_id}/resolve` 支持 `keep_host` 和
  `accept_terminal` 两种明确决策。
- `GET /api/sync/status` 显示节点、待发送数量和冲突数量。
- 同步前后仍应使用 `scripts/safe_backup_sqlite.py` 备份每台电脑。
- 主机故障时先恢复主机备份，不要指定任意终端数据库为新主机后直接覆盖其他终端。
- 不计划同步附件二进制；数据库中必须保留重新生成业务文档所需的完整业务数据。
