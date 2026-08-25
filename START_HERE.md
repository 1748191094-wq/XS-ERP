# 程序启动说明

## 第一次运行

建议安装 64 位 Python 3.11 或 3.12，然后在本目录打开 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
```

如果电脑没有 `py` 命令，可以把第一行改成：

```powershell
python -m venv .venv
```

## 管理员单机模式

直接双击根目录的 `启动-单机模式.cmd`，或者执行：

```powershell
.\.venv\Scripts\python.exe scripts\run_host.py --standalone
```

浏览器打开：`http://127.0.0.1:8000/`

系统已启用每日 02:30 校验备份；管理员可在“系统管理 → 数据备份”修改保留天数、份数和异地副本目录。

## 局域网协作模式

在管理员主机双击 `启动-局域网模式.cmd`，或者执行：

```powershell
.\.venv\Scripts\python.exe scripts\run_host.py --allow-lan
```

控制台和“主机与局域网”页面会显示成员访问地址，例如：

```text
http://192.168.1.20:8000/
```

成员电脑可以直接在浏览器打开该地址，也可以使用根目录的 `member_client.html`。成员端只通过主机 API 工作，不要复制或打开 `repair_management.db`。

## 停止程序

回到启动窗口按 `Ctrl+C`，等待服务退出后再关闭窗口。数据库迁移、复制或恢复前也必须先停止程序。

## 常见问题

- 提示找不到依赖：如果已创建虚拟环境，执行 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`；否则使用报错信息中显示的 Python 绝对路径执行安装命令。
- 端口被占用：关闭旧的启动窗口，或使用 `scripts\run_host.py --standalone --port 8001`。
- 局域网无法连接：确认成员电脑与主机在同一网络，并允许 Windows 防火墙放行所用端口；不要把端口映射到公网。
- 启动前无需运行旧程序 `legacy_quote_app.py`。本交付不包含旧数据库或历史业务数据；若需迁移，应由管理员另行选择并备份自有旧库。
