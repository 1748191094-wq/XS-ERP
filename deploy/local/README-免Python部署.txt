服务管理系统 - Windows 免 Python 部署

一、系统要求

- Windows 10/11 64 位。
- 不需要安装 Python，不需要 pip，不需要联网安装依赖。
- 整个 ServiceManager 文件夹必须完整保留，不能只复制 EXE。

二、主机

1. 将完整文件夹复制到管理员主机，例如 D:\ServiceManager。
2. 如果需要沿用旧数据，把 repair_management.db 放到 ServiceManager.exe 同级目录。
3. 双击“免Python-启动主机.cmd”。
4. 首次运行会自动创建 .env 和随机同步密钥。
5. 将“同步密钥.txt”安全地提供给本门店终端，不要公开发送。
6. 程序会自动打开 http://127.0.0.1:8000/。

三、终端

1. 每台终端单独复制一份完整文件夹，不要复制主机数据库。
2. 双击“免Python-启动终端.cmd”。
3. 首次运行按提示填写终端名称、主机局域网 IP 和主机同步密钥。
4. 终端会启动本地页面，并在后台每 5 分钟汇总数据库变更。

四、数据位置

- 数据库：repair_management.db
- 配置：.env
- 上传文件：uploads
- PDF：output\pdf
- 邮件快照：output\email_snapshots
- 数据库备份：backups
- 同步日志：logs\sync-agent.log

这些目录都位于 ServiceManager.exe 同级目录。升级程序时保留上述数据文件和目录。

五、数据库安全

- 每次发现数据库版本需要升级时，程序会先创建 SQLite 在线备份。
- 备份会执行完整性检查和 SHA-256 校验。
- 备份成功后才执行 Alembic 升级。
- 附件文件不参与多端同步；报价单和报告可根据数据库重新生成。

六、管理员账户紧急恢复

- 仅在管理主机本机双击“免Python-恢复管理员账户.cmd”。
- 按提示选择管理员、输入“恢复 用户名”确认，并设置新的强密码。
- 恢复前会强制创建并校验数据库备份；成功后撤销该管理员的全部登录会话并写入审计日志。
- 此能力不提供远程 HTTP 接口，也不存在通用后门密码。

七、常见问题

- Windows 安全提示：点击“更多信息”后确认文件来自本项目再运行。
- 端口被占用：关闭重复启动的 ServiceManager 窗口，或修改 .env 的 SERVER_PORT。
- 终端不同步：检查主机 IP、同步密钥、防火墙 8000 端口及 logs\sync-agent.log。
- 不要把 ServiceManager.exe 单独移出文件夹；_internal 目录包含运行时和依赖。
