from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "tmp" / "pdfs"
OUT = ROOT / "output" / "pdf"
OUT.mkdir(parents=True, exist_ok=True)

FONT_REGULAR = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")
pdfmetrics.registerFont(TTFont("SRV-Regular", str(FONT_REGULAR)))
pdfmetrics.registerFont(TTFont("SRV-Bold", str(FONT_BOLD)))

BLUE = HexColor("#168CFF")
BLUE_DARK = HexColor("#075BB8")
CYAN = HexColor("#58D7FF")
INK = HexColor("#171A22")
INK_2 = HexColor("#3E4655")
MUTED = HexColor("#6B7280")
PAPER = HexColor("#F4F6FA")
CARD = HexColor("#FFFFFF")
LINE = HexColor("#DCE2EA")
GREEN = HexColor("#0D9F6E")
AMBER = HexColor("#E99619")
RED = HexColor("#C84040")
SIDEBAR = HexColor("#17181D")

MANUAL_PATH = OUT / "服务平台科技服务工作台-使用说明书.pdf"
BROCHURE_PATH = OUT / "服务平台科技服务工作台-宣传手册.pdf"


def wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and pdfmetrics.stringWidth(candidate, font, size) > width:
                lines.append(current.rstrip())
                current = char.lstrip() if char == " " else char
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
    return lines


def text_block(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    width: float,
    *,
    font: str = "SRV-Regular",
    size: float = 9.5,
    leading: float = 15,
    color: Color = INK_2,
    max_lines: int | None = None,
) -> float:
    lines = wrap_text(text, font, size, width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while pdfmetrics.stringWidth(last + "…", font, size) > width and last:
            last = last[:-1]
        lines[-1] = last + "…"
    c.setFont(font, size)
    c.setFillColor(color)
    for line in lines:
        c.drawString(x, y, line)
        y -= leading
    return y


def pill(c: canvas.Canvas, text: str, x: float, y: float, bg: Color, fg: Color = white) -> float:
    w = pdfmetrics.stringWidth(text, "SRV-Bold", 7.5) + 10 * mm
    c.setFillColor(bg)
    c.roundRect(x, y - 4.2 * mm, w, 6.5 * mm, 3.2 * mm, fill=1, stroke=0)
    c.setFillColor(fg)
    c.setFont("SRV-Bold", 7.5)
    c.drawCentredString(x + w / 2, y - 1.8 * mm, text)
    return x + w + 2.5 * mm


def page_chrome(c: canvas.Canvas, page: int, section: str, *, pagesize=A4) -> None:
    w, h = pagesize
    c.setFillColor(PAPER)
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(SIDEBAR)
    c.rect(0, h - 18 * mm, w, 18 * mm, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.roundRect(13 * mm, h - 13.5 * mm, 8 * mm, 8 * mm, 2.2 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("SRV-Bold", 7)
    c.drawCentredString(17 * mm, h - 10.8 * mm, "服")
    c.setFont("SRV-Bold", 9.2)
    c.drawString(25 * mm, h - 10.7 * mm, "服务平台服务工作台")
    c.setFont("SRV-Regular", 7.2)
    c.setFillColor(HexColor("#AEB7C7"))
    c.drawRightString(w - 13 * mm, h - 10.5 * mm, section)
    c.setStrokeColor(LINE)
    c.line(13 * mm, 11 * mm, w - 13 * mm, 11 * mm)
    c.setFillColor(MUTED)
    c.setFont("SRV-Regular", 6.7)
    c.drawString(13 * mm, 6.5 * mm, "版本 2026.08 · 截图使用隔离演示数据")
    c.drawRightString(w - 13 * mm, 6.5 * mm, f"{page:02d}")


def page_title(c: canvas.Canvas, eyebrow: str, title: str, subtitle: str = "", *, pagesize=A4) -> float:
    _, h = pagesize
    y = h - 31 * mm
    c.setFillColor(BLUE_DARK)
    c.setFont("SRV-Bold", 7.2)
    c.drawString(14 * mm, y, eyebrow.upper())
    y -= 10 * mm
    c.setFillColor(INK)
    c.setFont("SRV-Bold", 23)
    c.drawString(14 * mm, y, title)
    if subtitle:
        y -= 8 * mm
        y = text_block(c, subtitle, 14 * mm, y, 180 * mm, size=9.5, leading=14, color=MUTED)
    return y - 4 * mm


def card(c: canvas.Canvas, x: float, y: float, w: float, h: float, *, fill=CARD, stroke=LINE, radius=4 * mm) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=1)


def draw_screenshot_fit(c: canvas.Canvas, image_path: Path, x: float, y: float, w: float, h: float) -> None:
    """Draw a UI screenshot without cropping any part of the application window."""
    with Image.open(image_path) as img:
        iw, ih = img.size
    scale = min(w / iw, h / ih)
    dw, dh = iw * scale, ih * scale
    dx, dy = x + (w - dw) / 2, y + (h - dh) / 2
    c.drawImage(str(image_path), dx, dy, width=dw, height=dh, mask="auto")


def bullet_list(c: canvas.Canvas, items: Iterable[str], x: float, y: float, width: float, *, size=9, gap=4) -> float:
    for item in items:
        c.setFillColor(BLUE)
        c.circle(x + 1.5 * mm, y + 1.1 * mm, 1.1 * mm, fill=1, stroke=0)
        y = text_block(c, item, x + 6 * mm, y + 2 * mm, width - 6 * mm, size=size, leading=size + 5, color=INK_2)
        y -= gap
    return y


def numbered_step(c: canvas.Canvas, n: int, title: str, body: str, x: float, y: float, w: float) -> None:
    c.setFillColor(BLUE)
    c.circle(x + 5 * mm, y - 5 * mm, 5 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("SRV-Bold", 9)
    c.drawCentredString(x + 5 * mm, y - 7 * mm, str(n))
    c.setFillColor(INK)
    c.setFont("SRV-Bold", 10.5)
    c.drawString(x + 13 * mm, y - 3.4 * mm, title)
    text_block(c, body, x + 13 * mm, y - 9.5 * mm, w - 13 * mm, size=8.2, leading=12, color=MUTED, max_lines=3)


def label_value(c: canvas.Canvas, label: str, value: str, x: float, y: float, width: float) -> float:
    c.setFont("SRV-Bold", 8)
    c.setFillColor(INK)
    c.drawString(x, y, label)
    return text_block(c, value, x, y - 5 * mm, width, size=8.2, leading=12, color=MUTED)


def manual_cover(c: canvas.Canvas) -> None:
    w, h = A4
    c.setFillColor(SIDEBAR)
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(BLUE_DARK)
    c.circle(w - 18 * mm, h - 20 * mm, 62 * mm, fill=1, stroke=0)
    c.setFillColor(Color(0.09, 0.57, 1, alpha=0.25))
    c.circle(w - 34 * mm, h - 55 * mm, 78 * mm, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.roundRect(16 * mm, h - 34 * mm, 13 * mm, 13 * mm, 3.5 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("SRV-Bold", 10)
    c.drawCentredString(22.5 * mm, h - 29.5 * mm, "服")
    c.setFont("SRV-Bold", 11)
    c.drawString(34 * mm, h - 28 * mm, "服务平台")
    c.setFillColor(HexColor("#AEB7C7"))
    c.setFont("SRV-Regular", 7.5)
    c.drawString(34 * mm, h - 33 * mm, "DIGITAL SERVICE WORKBENCH")
    c.setFillColor(CYAN)
    c.setFont("SRV-Bold", 8)
    c.drawString(16 * mm, h - 61 * mm, "OPERATION MANUAL")
    c.setFillColor(white)
    c.setFont("SRV-Bold", 30)
    c.drawString(16 * mm, h - 78 * mm, "服务工作台")
    c.drawString(16 * mm, h - 94 * mm, "使用说明书")
    text_block(c, "面向门店管理员、前台、维修工程师、财务与库管的业务操作指南", 16 * mm, h - 109 * mm, 170 * mm, size=11, leading=18, color=HexColor("#C8D0DE"))
    card(c, 16 * mm, 34 * mm, w - 32 * mm, 99 * mm, fill=HexColor("#242731"), stroke=HexColor("#343846"), radius=5 * mm)
    draw_screenshot_fit(c, TMP / "dashboard.png", 20 * mm, 38 * mm, w - 40 * mm, 91 * mm)
    c.setFillColor(HexColor("#AEB7C7"))
    c.setFont("SRV-Regular", 7.5)
    c.drawString(16 * mm, 20 * mm, "版本日期：2026-08-03")
    c.drawRightString(w - 16 * mm, 20 * mm, "本手册界面数据均为合成演示数据")
    c.showPage()


def make_manual() -> None:
    c = canvas.Canvas(str(MANUAL_PATH), pagesize=A4, pageCompression=1)
    c.setTitle("服务平台服务工作台使用说明书")
    c.setAuthor("服务平台")
    manual_cover(c)

    # 02 - workflow
    page_chrome(c, 2, "业务全景")
    y = page_title(c, "01 · OVERVIEW", "先看业务全景", "系统按门店服务流程组织功能，高频操作集中在首页与待办中心。")
    steps = [
        ("接待建档", "客户、设备、收机状态、附件与隐私备注"),
        ("检测定损", "日志、SOP、点位图、诊断与人工确认"),
        ("报价确认", "多版本报价、折扣、人工/运费、客户确认"),
        ("采购备料", "供应商、采购单、分批到货、批次与序列号"),
        ("维修协作", "维修工单、服务工单、专员升级、SLA 与时间线"),
        ("收款核算", "收入、支出、退款、领料成本与毛利润"),
        ("物流交付", "人工轨迹、签收、异常、邮件与报告"),
        ("回访复盘", "回访任务、客户时间线、经营报表与审计"),
    ]
    for i, (title, body) in enumerate(steps):
        col, row = i % 2, i // 2
        x = 14 * mm + col * 92 * mm
        yy = y - row * 42 * mm
        card(c, x, yy - 31 * mm, 86 * mm, 35 * mm)
        numbered_step(c, i + 1, title, body, x + 5 * mm, yy - 3 * mm, 76 * mm)
    card(c, 14 * mm, 24 * mm, 182 * mm, 27 * mm, fill=HexColor("#EAF4FF"), stroke=HexColor("#BBDDFC"))
    c.setFillColor(BLUE_DARK)
    c.setFont("SRV-Bold", 10)
    c.drawString(20 * mm, 42 * mm, "建议从“仪表盘 → 待办中心 → 快捷录入”开始")
    text_block(c, "角色权限会自动控制可见页面与可执行操作。若菜单与本手册不完全一致，请以管理员为当前账号配置的角色为准。", 20 * mm, 34 * mm, 168 * mm, size=8.5, leading=12, color=INK_2)
    c.showPage()

    # 03 - startup
    page_chrome(c, 3, "安装与登录")
    y = page_title(c, "02 · GET STARTED", "启动、登录与协作模式", "日常用户只需通过浏览器访问；数据库只由管理员主机服务访问。")
    card(c, 14 * mm, y - 58 * mm, 86 * mm, 58 * mm)
    c.setFont("SRV-Bold", 12); c.setFillColor(INK); c.drawString(20 * mm, y - 10 * mm, "管理员单机模式")
    bullet_list(c, ["双击“启动-单机模式.cmd”", "浏览器访问 http://127.0.0.1:8000/", "适合一台电脑独立使用"], 20 * mm, y - 21 * mm, 72 * mm, size=8.5)
    card(c, 110 * mm, y - 58 * mm, 86 * mm, 58 * mm)
    c.setFont("SRV-Bold", 12); c.setFillColor(INK); c.drawString(116 * mm, y - 10 * mm, "局域网协作模式")
    bullet_list(c, ["主机双击“启动-局域网模式.cmd”", "成员浏览器打开主机显示的局域网地址", "成员端不要复制或直接打开 SQLite 数据库"], 116 * mm, y - 21 * mm, 72 * mm, size=8.5)
    yy = y - 71 * mm
    c.setFont("SRV-Bold", 13); c.setFillColor(INK); c.drawString(14 * mm, yy, "首次登录")
    for i, (title, body) in enumerate([
        ("初始化管理员", "首次空数据库会显示创建管理员页；系统不内置默认弱密码。"),
        ("输入账号", "使用门店分配的用户名与密码，登录名匹配不区分大小写。"),
        ("确认角色", "右上角会显示姓名与角色；不同角色对应不同权限。"),
        ("安全退出", "公共或共享电脑工作结束后，从账户菜单安全退出。"),
    ]):
        x = 14 * mm + (i % 2) * 92 * mm
        ystep = yy - 11 * mm - (i // 2) * 32 * mm
        numbered_step(c, i + 1, title, body, x, ystep, 84 * mm)
    card(c, 14 * mm, 28 * mm, 182 * mm, 37 * mm, fill=HexColor("#FFF7E6"), stroke=HexColor("#F0D39A"))
    c.setFillColor(AMBER); c.setFont("SRV-Bold", 10); c.drawString(20 * mm, 55 * mm, "运行与数据安全")
    bullet_list(c, ["停止服务：回到启动窗口按 Ctrl+C，并等待服务退出后再关窗。", "数据库迁移、复制或恢复前必须先停止程序；日常备份请使用系统的在线备份功能。", "不要将局域网端口直接映射到公网；公网部署需 HTTPS、反向代理与服务器级防护。"], 20 * mm, 45 * mm, 168 * mm, size=8.2, gap=2)
    c.showPage()

    # 04 - interface
    page_chrome(c, 4, "界面基础")
    y = page_title(c, "03 · NAVIGATION", "一眼看懂主界面", "导航按业务流程分组；全局检索、快捷录入和写邮件始终位于顶栏。")
    card(c, 14 * mm, 88 * mm, 182 * mm, 109 * mm)
    draw_screenshot_fit(c, TMP / "dashboard.png", 18 * mm, 92 * mm, 174 * mm, 101 * mm)
    yy = 79 * mm
    cols = [
        ("① 左侧导航", "业务中心、工单与服务、报价与经营、技术与质检、交付与关系、系统管理。"),
        ("② 顶栏工具", "全局检索可找工单号、报价号、客户、序列号或邮件；右侧账户菜单用于刷新、改密与退出。"),
        ("③ 首页快捷动作", "新建维修/服务工单、发送工单邮件、记录收支，减少跨页跳转。"),
    ]
    for i, (title, body) in enumerate(cols):
        x = 14 * mm + i * 61 * mm
        c.setFillColor(INK); c.setFont("SRV-Bold", 9.5); c.drawString(x, yy, title)
        text_block(c, body, x, yy - 6 * mm, 55 * mm, size=7.7, leading=11, color=MUTED)
    c.showPage()

    # 05 - quick entry
    page_chrome(c, 5, "快捷录入")
    y = page_title(c, "04 · INTAKE", "一次提交完成收机建单", "快捷录入会以事务方式创建或复用客户、绑定设备、建立维修工单与报价版本。")
    card(c, 14 * mm, 91 * mm, 182 * mm, 106 * mm)
    draw_screenshot_fit(c, TMP / "quick-entry.png", 18 * mm, 95 * mm, 174 * mm, 98 * mm)
    yy = 82 * mm
    tips = [
        ("必填", "客户姓名、设备型号、故障描述；报价项目至少写清名称。"),
        ("推荐", "电话/邮箱、序列号、收机外观、随附配件、质保状态和优先级。"),
        ("检查", "右侧实时预览客户、设备、故障和金额；提交前核对折扣、人工费、运费。"),
        ("可选", "生成报价 PDF；邮件仅在地址有效且投递模式配置完成后使用。"),
    ]
    for i, (title, body) in enumerate(tips):
        x = 14 * mm + (i % 2) * 92 * mm
        yy2 = yy - (i // 2) * 31 * mm
        card(c, x, yy2 - 24 * mm, 86 * mm, 26 * mm, fill=HexColor("#F8FAFD"))
        c.setFillColor(BLUE_DARK); c.setFont("SRV-Bold", 9); c.drawString(x + 5 * mm, yy2 - 7 * mm, title)
        text_block(c, body, x + 5 * mm, yy2 - 13 * mm, 76 * mm, size=7.8, leading=11, color=MUTED, max_lines=3)
    c.showPage()

    # 06 - work center
    page_chrome(c, 6, "待办中心")
    y = page_title(c, "05 · UNIFIED INBOX", "今天要处理的事集中在一处", "维修、服务、回访、物流、失败邮件和库存预警汇总为统一待办。")
    card(c, 14 * mm, 89 * mm, 182 * mm, 108 * mm)
    draw_screenshot_fit(c, TMP / "work-center.png", 18 * mm, 93 * mm, 174 * mm, 100 * mm)
    yy = 79 * mm
    bullet_list(c, [
        "筛选“全部 / 我负责的 / 未分派 / 已超时”，先处理高优先级或接近 SLA 的事项。",
        "勾选多条待办后可批量分派、批量改状态或批量设置时限；批量操作前再次确认对象范围。",
        "点击星标收藏常用工单；最近查看与收藏会在仪表盘提供快速返回入口。",
        "维修工单与服务工单可以并行存在：前者记录维修状态，后者记录协作、沟通与升级过程。",
    ], 16 * mm, yy, 176 * mm, size=8.3, gap=2)
    c.showPage()

    # 07 - tickets
    page_chrome(c, 7, "工单与服务")
    y = page_title(c, "06 · SERVICE FLOW", "维修工单与统一服务工单", "用维修状态描述设备进度，用服务工单承载负责人、协作、SLA、备注与客户沟通。")
    stages = ["待检测", "检测中", "待报价", "已报价", "客户已确认", "维修中", "待测试", "待发货", "已完成"]
    x0, yy = 14 * mm, y
    for i, stage in enumerate(stages):
        x = x0 + (i % 3) * 61 * mm
        sy = yy - (i // 3) * 25 * mm
        card(c, x, sy - 16 * mm, 55 * mm, 19 * mm, fill=HexColor("#F7FAFE"))
        c.setFillColor(BLUE); c.circle(x + 6 * mm, sy - 6.5 * mm, 3.2 * mm, fill=1, stroke=0)
        c.setFillColor(INK); c.setFont("SRV-Bold", 9); c.drawString(x + 12 * mm, sy - 8.5 * mm, stage)
        if i < len(stages) - 1 and i % 3 != 2:
            c.setFillColor(MUTED); c.setFont("SRV-Regular", 9); c.drawString(x + 56 * mm, sy - 8 * mm, "→")
    yy2 = y - 83 * mm
    card(c, 14 * mm, yy2 - 68 * mm, 86 * mm, 68 * mm)
    c.setFillColor(INK); c.setFont("SRV-Bold", 12); c.drawString(20 * mm, yy2 - 10 * mm, "服务工单")
    bullet_list(c, ["覆盖咨询、报价跟进、投诉、物流异常、技术支持、零售与高级专员协助", "记录负责人、协助成员、处理组、优先级、SLA、催办与时间线", "备注分为内部可见与客户可见，避免沟通口径混淆"], 20 * mm, yy2 - 22 * mm, 72 * mm, size=8.1, gap=2)
    card(c, 110 * mm, yy2 - 68 * mm, 86 * mm, 68 * mm)
    c.setFillColor(INK); c.setFont("SRV-Bold", 12); c.drawString(116 * mm, yy2 - 10 * mm, "高级专员升级")
    bullet_list(c, ["提交问题摘要、升级原因与已尝试方案", "指定专员或处理组，可退回补充、接单、给出方案", "最终意见、结果与完成时间独立留痕"], 116 * mm, yy2 - 22 * mm, 72 * mm, size=8.1, gap=2)
    c.showPage()

    # 08 - quote/finance
    page_chrome(c, 8, "报价与财务")
    y = page_title(c, "07 · QUOTE & CASHFLOW", "报价、报告、邮件与收款分离", "客户确认报价不等于已收款；财务流水按实际发生记录。")
    items = [
        ("报价版本", "逐项维护配件、服务、人工、耗材和运费；支持折扣、历史版本与客户确认。"),
        ("统一 PDF", "报价单、检测报告、完成报告共享品牌页眉页脚、信息卡、长文本与多页表格。"),
        ("邮件中心", "模板预览、人工修改、抄送/密送、报告附件快照、后台投递和有限重试。"),
        ("财务流水", "收入、支出、退款独立入账；系统结合实际收款、费用和领料成本重算毛利润。"),
    ]
    for i, (title, body) in enumerate(items):
        x = 14 * mm + (i % 2) * 92 * mm
        yy = y - (i // 2) * 51 * mm
        card(c, x, yy - 42 * mm, 86 * mm, 44 * mm)
        c.setFillColor(BLUE if i < 2 else GREEN); c.roundRect(x + 6 * mm, yy - 13 * mm, 11 * mm, 11 * mm, 3 * mm, fill=1, stroke=0)
        c.setFillColor(white); c.setFont("SRV-Bold", 8); c.drawCentredString(x + 11.5 * mm, yy - 9 * mm, ["¥", "PDF", "@", "账"][i])
        c.setFillColor(INK); c.setFont("SRV-Bold", 11); c.drawString(x + 22 * mm, yy - 9 * mm, title)
        text_block(c, body, x + 7 * mm, yy - 21 * mm, 72 * mm, size=8.2, leading=12, color=MUTED, max_lines=5)
    card(c, 14 * mm, 47 * mm, 182 * mm, 38 * mm, fill=HexColor("#FFF4F2"), stroke=HexColor("#F3C6C0"))
    c.setFillColor(RED); c.setFont("SRV-Bold", 9.5); c.drawString(20 * mm, 75 * mm, "外部投递边界")
    text_block(c, "SMTP 代码已内置，但真实投递必须先在系统设置中完成服务器、发件账号和授权码配置，并向专用测试地址验证。默认 Mock 模式不会向外发送。企业微信和顺丰未配置时同样不会伪造成功结果。", 20 * mm, 66 * mm, 168 * mm, size=8.2, leading=12, color=INK_2)
    c.showPage()

    # 09 - inventory
    page_chrome(c, 9, "库存与采购")
    y = page_title(c, "08 · SUPPLY CHAIN", "从采购到盘点的闭环", "入库更新库存与加权采购价，维修领料自动扣减并禁止负库存。")
    flow = [
        ("供应商", "维护联系人、结算与备注"), ("采购单", "草稿、下单、部分到货、完成"),
        ("分批到货", "批次、序列号、成本与数量"), ("采购退货", "退货数量、原因与库存回退"),
        ("付款", "记录付款进度与实际金额"), ("库存盘点", "全库盘点、差异与调整审计"),
    ]
    for i, (title, body) in enumerate(flow):
        x = 14 * mm + (i % 3) * 61 * mm
        yy = y - (i // 3) * 47 * mm
        card(c, x, yy - 38 * mm, 55 * mm, 40 * mm)
        c.setFillColor(BLUE); c.setFont("SRV-Bold", 18); c.drawString(x + 6 * mm, yy - 12 * mm, f"0{i+1}")
        c.setFillColor(INK); c.setFont("SRV-Bold", 10); c.drawString(x + 6 * mm, yy - 21 * mm, title)
        text_block(c, body, x + 6 * mm, yy - 28 * mm, 43 * mm, size=7.5, leading=10, color=MUTED, max_lines=3)
    yy = y - 101 * mm
    c.setFillColor(INK); c.setFont("SRV-Bold", 12); c.drawString(14 * mm, yy, "操作要点")
    bullet_list(c, [
        "库存项目建议统一 SKU；可使用扫码定位与批量操作减少手工搜索。",
        "维修领料必须关联工单，退料使用独立类型；每次变动记录变动前后数量、成本与操作人。",
        "低库存会进入统一待办；采购入库前核对供应商、采购单、到货批次、序列号与单位成本。",
        "盘点提交后再进行差异调整；重大差异应在备注中说明原因并保留审计证据。",
    ], 16 * mm, yy - 11 * mm, 176 * mm, size=8.3, gap=3)
    c.showPage()

    # 10 - technical
    page_chrome(c, 10, "技术与质检")
    y = page_title(c, "09 · TECHNICAL QA", "检测结果必须由工程师确认", "系统提供任务、规则与留痕，不替代工程师判断，也不伪造未接入工具的结果。")
    tech = [
        ("日志任务", "上传受限格式文件，记录后台任务、进度、错误和解析状态。"),
        ("定损 SOP", "按设备与故障场景执行标准检查步骤，留存人工结论。"),
        ("设备点位图", "批量导入参考资料，支持去重、检索、缩放与标记定位。"),
        ("诊断结果", "电池、电机、ESC、IMU、GPS、指南针与失联/返航规则分析。"),
        ("技术工具", "对工具路径、签名、哈希、授权与设备状态进行安全检查。"),
        ("标定记录", "记录手工或官方工具完成的标定过程、结果与复核。"),
    ]
    for i, (title, body) in enumerate(tech):
        x = 14 * mm + (i % 2) * 92 * mm
        yy = y - (i // 2) * 39 * mm
        card(c, x, yy - 31 * mm, 86 * mm, 33 * mm)
        c.setFillColor(INK); c.setFont("SRV-Bold", 10); c.drawString(x + 6 * mm, yy - 10 * mm, title)
        text_block(c, body, x + 6 * mm, yy - 18 * mm, 74 * mm, size=7.8, leading=11, color=MUTED, max_lines=3)
    card(c, 14 * mm, 33 * mm, 182 * mm, 38 * mm, fill=HexColor("#FFF4F2"), stroke=HexColor("#F3C6C0"))
    c.setFillColor(RED); c.setFont("SRV-Bold", 9.5); c.drawString(20 * mm, 61 * mm, "明确限制")
    text_block(c, "DJI DAT 私有格式继续标记为不支持；DJI 官方云台底层自动标定未实现，系统只记录人工或官方工具流程。PX4 ULog、ArduPilot BIN 和 DJI Flight Record v13 为可选适配，只有安装对应依赖并完成样本验证后才可启用。", 20 * mm, 52 * mm, 168 * mm, size=8.1, leading=12, color=INK_2)
    c.showPage()

    # 11 - delivery & reports
    page_chrome(c, 11, "交付与客户关系")
    y = page_title(c, "10 · DELIVERY", "从物流到回访形成客户时间线", "所有沟通与关键状态都应关联客户、设备或工单，便于后续追溯。")
    sections = [
        ("物流信息", ["人工轨迹、签收、异常与完整事件", "顺丰未配置时仅进入待配置队列，不生成虚假运单号"]),
        ("外呼与回访", ["人工外呼任务与通话结果", "回访完成后可生成下一次联系任务"]),
        ("客户详情", ["统一显示状态、备注、外呼、邮件、报价、物流与回访", "序列号可重复留存，支持二手设备流转记录"]),
        ("经营复盘", ["经营报表、全局检索、最近查看和收藏", "按角色查看授权范围内的数据与指标"]),
    ]
    for i, (title, bullets) in enumerate(sections):
        x = 14 * mm + (i % 2) * 92 * mm
        yy = y - (i // 2) * 64 * mm
        card(c, x, yy - 54 * mm, 86 * mm, 57 * mm)
        c.setFillColor(BLUE if i % 2 == 0 else GREEN); c.roundRect(x + 6 * mm, yy - 16 * mm, 18 * mm, 10 * mm, 3 * mm, fill=1, stroke=0)
        c.setFillColor(white); c.setFont("SRV-Bold", 8); c.drawCentredString(x + 15 * mm, yy - 12.5 * mm, ["运", "呼", "客", "报"][i])
        c.setFillColor(INK); c.setFont("SRV-Bold", 11); c.drawString(x + 29 * mm, yy - 13 * mm, title)
        bullet_list(c, bullets, x + 7 * mm, yy - 26 * mm, 72 * mm, size=7.8, gap=2)
    c.showPage()

    # 12 - governance
    page_chrome(c, 12, "系统治理")
    y = page_title(c, "11 · GOVERNANCE", "账号、审计、备份与主机", "系统治理页面通常仅向管理员或被授权角色开放。")
    governance = [
        ("账号管理", "管理员、门店经理、前台、维修工程师、财务、库管、只读查看等角色。"),
        ("操作审计", "新增、修改、发送、上传、退出等写操作留痕；不记录密码或 SMTP 授权码。"),
        ("数据备份", "SQLite 在线备份后执行完整性检查与 SHA-256；支持下载、复验和保留策略。"),
        ("回收站", "支持软删除记录的受控查看与恢复，避免把删除当作普通清理动作。"),
        ("主机与局域网", "显示访问地址、在线成员与网络开关；成员只访问 HTTP API。"),
        ("多端同步", "提供同步角色、节点和状态管理；使用前由管理员完成节点与共享密钥配置。"),
        ("系统设置", "邮件、企业微信、顺丰、PDF 品牌、备份与技术工具配置状态。"),
        ("恢复演练", "恢复前先停止服务；脚本会保留当前数据库保护副本并要求明确确认文本。"),
    ]
    for i, (title, body) in enumerate(governance):
        x = 14 * mm + (i % 2) * 92 * mm
        yy = y - (i // 2) * 34 * mm
        c.setFillColor(BLUE); c.circle(x + 3 * mm, yy - 4 * mm, 2.5 * mm, fill=1, stroke=0)
        c.setFillColor(INK); c.setFont("SRV-Bold", 9.5); c.drawString(x + 9 * mm, yy - 2.5 * mm, title)
        text_block(c, body, x + 9 * mm, yy - 9 * mm, 74 * mm, size=7.7, leading=11, color=MUTED, max_lines=3)
    c.showPage()

    # 13 - mobile
    page_chrome(c, 13, "移动端与协作")
    y = page_title(c, "12 · MOBILE", "手机上保留最短主路径", "移动端突出仪表盘、待办、快捷录入、维修工单与“更多”，适合到店接待和现场查看。")
    card(c, 14 * mm, 48 * mm, 70 * mm, 160 * mm, fill=SIDEBAR, stroke=HexColor("#30343D"), radius=8 * mm)
    draw_screenshot_fit(c, TMP / "mobile-dashboard.png", 20 * mm, 54 * mm, 58 * mm, 148 * mm)
    x = 96 * mm
    c.setFillColor(INK); c.setFont("SRV-Bold", 13); c.drawString(x, 192 * mm, "移动端建议")
    bullet_list(c, [
        "现场接待优先使用快捷录入，提交前在右侧/下方预览确认客户与报价。",
        "待办中心用于查看分派、超时和优先级；复杂批量处理建议回到桌面端。",
        "录入序列号、金额与客户联系方式时避免在公共场所外放或截屏分享。",
        "网络中断时停止重复提交，等待连接提示恢复后再检查是否已建单。",
    ], x, 180 * mm, 99 * mm, size=8.5, gap=4)
    card(c, x, 86 * mm, 98 * mm, 50 * mm, fill=HexColor("#EAF4FF"), stroke=HexColor("#BBDDFC"))
    c.setFillColor(BLUE_DARK); c.setFont("SRV-Bold", 10); c.drawString(x + 6 * mm, 124 * mm, "成员端连接原则")
    text_block(c, "成员电脑打开管理员主机显示的局域网地址，或使用 member_client.html。所有业务请求经主机 HTTP API 处理；数据库文件留在主机，不在成员电脑之间复制。", x + 6 * mm, 115 * mm, 86 * mm, size=8, leading=12, color=INK_2)
    c.showPage()

    # 14 - checklist
    page_chrome(c, 14, "检查清单与常见问题")
    y = page_title(c, "13 · CHECKLIST", "日常检查与故障处理", "先保护数据，再处理程序；无法确认时记录现象并联系管理员。")
    card(c, 14 * mm, y - 72 * mm, 86 * mm, 72 * mm)
    c.setFillColor(INK); c.setFont("SRV-Bold", 12); c.drawString(20 * mm, y - 11 * mm, "每日开工")
    bullet_list(c, ["确认主机服务运行中，成员端可正常访问", "查看待办中心：超时、未分派、低库存和失败邮件", "抽查最近备份状态与可用空间", "确认邮件/外部接口处于预期的 Mock 或已配置状态"], 20 * mm, y - 23 * mm, 72 * mm, size=8.1, gap=2)
    card(c, 110 * mm, y - 72 * mm, 86 * mm, 72 * mm)
    c.setFillColor(INK); c.setFont("SRV-Bold", 12); c.drawString(116 * mm, y - 11 * mm, "每日收工")
    bullet_list(c, ["核对新工单、收支与库存变动是否完整", "关闭未使用的管理员会话，公共电脑安全退出", "停止服务时使用 Ctrl+C 并等待程序退出", "重大操作或异常在审计/备注中留下可追溯说明"], 116 * mm, y - 23 * mm, 72 * mm, size=8.1, gap=2)
    yy = y - 83 * mm
    faqs = [
        ("提示找不到依赖", "使用实际启动器选择的 Python 安装 requirements.txt 中的依赖。"),
        ("端口被占用", "关闭旧启动窗口，或用 run_host.py 指定其他端口。"),
        ("局域网无法连接", "确认同一网络、防火墙放行端口，且不要暴露到公网。"),
        ("邮件没有发出", "先看邮件中心状态和系统设置诊断；Mock 模式不会外发。"),
        ("解析结果不支持", "检查文件格式与可选适配器；不支持的格式不会生成伪结果。"),
        ("需要恢复备份", "停止应用，选择已校验备份，由管理员执行恢复脚本并保留保护副本。"),
    ]
    for i, (q, a) in enumerate(faqs):
        x = 14 * mm + (i % 2) * 92 * mm
        fy = yy - (i // 2) * 31 * mm
        c.setFillColor(BLUE_DARK); c.setFont("SRV-Bold", 8.8); c.drawString(x, fy, q)
        text_block(c, a, x, fy - 6 * mm, 84 * mm, size=7.7, leading=11, color=MUTED, max_lines=3)
    c.save()


def brochure_chrome(c: canvas.Canvas, page: int, label: str) -> None:
    w, h = landscape(A4)
    c.setFillColor(PAPER); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(SIDEBAR); c.rect(0, h - 14 * mm, w, 14 * mm, fill=1, stroke=0)
    c.setFillColor(BLUE); c.roundRect(12 * mm, h - 10.5 * mm, 7 * mm, 7 * mm, 2 * mm, fill=1, stroke=0)
    c.setFillColor(white); c.setFont("SRV-Bold", 6.5); c.drawCentredString(15.5 * mm, h - 8.2 * mm, "服")
    c.setFont("SRV-Bold", 8.5); c.drawString(23 * mm, h - 8.7 * mm, "服务平台服务工作台")
    c.setFillColor(HexColor("#AEB7C7")); c.setFont("SRV-Regular", 7); c.drawRightString(w - 12 * mm, h - 8.5 * mm, label)
    c.setFillColor(MUTED); c.setFont("SRV-Regular", 6.5); c.drawString(12 * mm, 6 * mm, "合成演示数据 · 版本 2026.08")
    c.drawRightString(w - 12 * mm, 6 * mm, f"{page:02d}")


def brochure_title(c: canvas.Canvas, eyebrow: str, title: str, subtitle: str = "") -> float:
    _, h = landscape(A4)
    y = h - 27 * mm
    c.setFillColor(BLUE_DARK); c.setFont("SRV-Bold", 7.5); c.drawString(12 * mm, y, eyebrow)
    c.setFillColor(INK); c.setFont("SRV-Bold", 24); c.drawString(12 * mm, y - 11 * mm, title)
    if subtitle:
        text_block(c, subtitle, 12 * mm, y - 20 * mm, 260 * mm, size=9, leading=13, color=MUTED)
    return y - 29 * mm


def make_brochure() -> None:
    size = landscape(A4)
    w, h = size
    c = canvas.Canvas(str(BROCHURE_PATH), pagesize=size, pageCompression=1)
    c.setTitle("服务平台服务工作台宣传手册")
    c.setAuthor("服务平台")

    # 01 cover
    c.setFillColor(SIDEBAR); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(BLUE_DARK); c.circle(w - 28 * mm, h - 15 * mm, 72 * mm, fill=1, stroke=0)
    c.setFillColor(Color(0.09, 0.57, 1, alpha=0.28)); c.circle(w - 60 * mm, h - 48 * mm, 92 * mm, fill=1, stroke=0)
    c.setFillColor(BLUE); c.roundRect(14 * mm, h - 25 * mm, 11 * mm, 11 * mm, 3 * mm, fill=1, stroke=0)
    c.setFillColor(white); c.setFont("SRV-Bold", 8.5); c.drawCentredString(19.5 * mm, h - 21.2 * mm, "服")
    c.setFont("SRV-Bold", 10); c.drawString(30 * mm, h - 20 * mm, "服务平台")
    c.setFillColor(CYAN); c.setFont("SRV-Bold", 8); c.drawString(14 * mm, h - 52 * mm, "DIGITAL SERVICE WORKBENCH")
    c.setFillColor(white); c.setFont("SRV-Bold", 28); c.drawString(14 * mm, h - 67 * mm, "把门店服务，")
    c.drawString(14 * mm, h - 82 * mm, "变成一条可追溯的业务链")
    text_block(c, "从接待、检测、报价、采购、维修到交付、回访与经营复盘，一个工作台连接人员、设备、库存与现金流。", 14 * mm, h - 96 * mm, 132 * mm, size=11, leading=17, color=HexColor("#C8D0DE"))
    pill(c, "本地部署", 14 * mm, 42 * mm, BLUE)
    x2 = pill(c, "角色权限", 47 * mm, 42 * mm, GREEN)
    pill(c, "审计备份", x2, 42 * mm, HexColor("#805AD5"))
    card(c, 160 * mm, 54 * mm, 121 * mm, 90 * mm, fill=HexColor("#242731"), stroke=HexColor("#343846"), radius=7 * mm)
    draw_screenshot_fit(c, TMP / "dashboard.png", 166 * mm, 60 * mm, 109 * mm, 78 * mm)
    c.setFillColor(HexColor("#AEB7C7")); c.setFont("SRV-Regular", 7); c.drawString(14 * mm, 15 * mm, "产品宣传手册 · 2026.08")
    c.showPage()

    # 02 flow
    brochure_chrome(c, 2, "全流程闭环")
    y = brochure_title(c, "ONE WORKFLOW", "一条业务链，八个关键环节", "按门店真实流程组织工作，减少重复录入和信息断点。")
    flow = [("01", "接待建档"), ("02", "检测定损"), ("03", "报价确认"), ("04", "采购备料"), ("05", "维修协作"), ("06", "收款核算"), ("07", "物流交付"), ("08", "回访复盘")]
    for i, (num, title) in enumerate(flow):
        x = 12 * mm + i * 34.5 * mm
        card(c, x, 74 * mm, 29 * mm, 55 * mm, fill=CARD)
        c.setFillColor(BLUE if i < 5 else GREEN); c.setFont("SRV-Bold", 18); c.drawCentredString(x + 14.5 * mm, 111 * mm, num)
        c.setFillColor(INK); c.setFont("SRV-Bold", 9); c.drawCentredString(x + 14.5 * mm, 96 * mm, title)
        c.setFillColor(MUTED); c.setFont("SRV-Regular", 12); c.drawCentredString(x + 14.5 * mm, 82 * mm, "→" if i < 7 else "完成")
    card(c, 12 * mm, 25 * mm, 273 * mm, 34 * mm, fill=HexColor("#EAF4FF"), stroke=HexColor("#BBDDFC"))
    c.setFillColor(BLUE_DARK); c.setFont("SRV-Bold", 11); c.drawString(20 * mm, 47 * mm, "一个客户、一台设备、一张工单，持续累积可追溯的服务时间线")
    text_block(c, "维修状态描述设备进度，服务工单承载负责人、协作、SLA 与沟通；报价、库存和财务按实际业务分别留痕，最后汇入经营报表。", 20 * mm, 38 * mm, 255 * mm, size=8.5, leading=12, color=INK_2)
    c.showPage()

    # 03 capabilities
    brochure_chrome(c, 3, "核心能力")
    y = brochure_title(c, "CAPABILITIES", "六大业务域，覆盖门店日常", "从前台到后台，从工程师到经营者，每个角色都有清晰工作区。")
    caps = [
        ("接待与客户", "快捷录入 · 客户/设备档案 · 全局检索 · 最近查看"),
        ("工单与协作", "维修/服务工单 · 待办中心 · SLA · 专员升级"),
        ("报价与现金流", "多版本报价 · PDF/邮件 · 收支退款 · 毛利润"),
        ("库存与采购", "供应商 · 分批到货 · 批次序列 · 退货付款 · 盘点"),
        ("技术与质检", "日志任务 · SOP · 点位图 · 诊断 · 标定记录"),
        ("治理与部署", "角色权限 · 审计 · 在线备份 · 局域网 · 多端同步"),
    ]
    for i, (title, body) in enumerate(caps):
        x = 12 * mm + (i % 3) * 92 * mm
        yy = 121 * mm - (i // 3) * 62 * mm
        card(c, x, yy - 47 * mm, 86 * mm, 51 * mm)
        c.setFillColor(BLUE if i < 3 else GREEN); c.roundRect(x + 7 * mm, yy - 14 * mm, 14 * mm, 14 * mm, 4 * mm, fill=1, stroke=0)
        c.setFillColor(white); c.setFont("SRV-Bold", 8); c.drawCentredString(x + 14 * mm, yy - 9.5 * mm, ["客", "单", "¥", "库", "技", "盾"][i])
        c.setFillColor(INK); c.setFont("SRV-Bold", 11); c.drawString(x + 27 * mm, yy - 9 * mm, title)
        text_block(c, body, x + 7 * mm, yy - 24 * mm, 72 * mm, size=8, leading=12, color=MUTED, max_lines=3)
    c.showPage()

    # 04 experience
    brochure_chrome(c, 4, "产品体验")
    y = brochure_title(c, "DESIGNED FOR FOCUS", "高频入口前置，复杂信息分层", "桌面端适合批量处理与完整台账；统一待办让团队聚焦今天必须完成的事。")
    card(c, 12 * mm, 30 * mm, 132 * mm, 112 * mm)
    draw_screenshot_fit(c, TMP / "dashboard.png", 16 * mm, 34 * mm, 124 * mm, 104 * mm)
    card(c, 153 * mm, 30 * mm, 132 * mm, 112 * mm)
    draw_screenshot_fit(c, TMP / "work-center.png", 157 * mm, 34 * mm, 124 * mm, 104 * mm)
    c.setFillColor(INK); c.setFont("SRV-Bold", 9); c.drawString(18 * mm, 22 * mm, "仪表盘：快捷动作、经营概览、最近工单")
    c.drawString(159 * mm, 22 * mm, "统一待办：负责、未分派、超时与批量操作")
    c.showPage()

    # 05 efficiency/mobile
    brochure_chrome(c, 5, "效率与移动端")
    y = brochure_title(c, "LESS FRICTION", "一次录入，桌面与移动端连续协作", "收机信息、设备、故障和报价实时预览，降低漏项与重复建档。")
    card(c, 12 * mm, 29 * mm, 184 * mm, 113 * mm)
    draw_screenshot_fit(c, TMP / "quick-entry.png", 16 * mm, 33 * mm, 176 * mm, 105 * mm)
    card(c, 207 * mm, 24 * mm, 50 * mm, 126 * mm, fill=SIDEBAR, stroke=HexColor("#30343D"), radius=7 * mm)
    draw_screenshot_fit(c, TMP / "mobile-dashboard.png", 212 * mm, 30 * mm, 40 * mm, 114 * mm)
    c.setFillColor(INK); c.setFont("SRV-Bold", 11); c.drawString(264 * mm, 125 * mm, "移动端保留")
    bullet_list(c, ["仪表盘", "待办中心", "快捷录入", "维修工单", "更多功能"], 264 * mm, 113 * mm, 21 * mm, size=8.2, gap=3)
    c.setFillColor(MUTED); c.setFont("SRV-Regular", 6.5); c.drawString(12 * mm, 20 * mm, "界面截图来自隔离演示库，所有客户名称、设备编号与金额均为合成数据。")
    c.showPage()

    # 06 trust
    brochure_chrome(c, 6, "可信与边界")
    y = brochure_title(c, "TRUST BY DESIGN", "把安全与真实性写进系统边界", "可验证的能力明确展示；需要配置或尚未接入的能力不伪造成功。")
    cols = [
        ("已内置", GREEN, ["角色与后端权限隔离", "写操作审计", "SQLite 在线备份与完整性复验", "报价/检测/完成 PDF", "Mock 邮件与集成诊断"]),
        ("需管理员配置", BLUE, ["SMTP 真实投递", "局域网与同步节点", "PDF 品牌与付款链接", "可选日志解析依赖", "异地备份目录"]),
        ("明确未伪造", RED, ["未配置企业微信不发送", "未配置顺丰不创建运单", "DJI DAT 私有格式不支持", "不声称完成 DJI 底层自动标定", "分析结果要求工程师确认"]),
    ]
    for i, (title, color, items) in enumerate(cols):
        x = 12 * mm + i * 92 * mm
        card(c, x, 34 * mm, 86 * mm, 104 * mm)
        c.setFillColor(color); c.roundRect(x + 7 * mm, 118 * mm, 31 * mm, 10 * mm, 5 * mm, fill=1, stroke=0)
        c.setFillColor(white); c.setFont("SRV-Bold", 8.5); c.drawCentredString(x + 22.5 * mm, 121.5 * mm, title)
        bullet_list(c, items, x + 7 * mm, 108 * mm, 72 * mm, size=8.1, gap=4)
    c.showPage()

    # 07 deployment/value
    brochure_chrome(c, 7, "部署与价值")
    y = brochure_title(c, "BUILT FOR REAL STORES", "从单机到局域网，保留清晰升级路径", "单店与轻量协作可使用 SQLite；业务量和并发增长后可评估 PostgreSQL。")
    card(c, 12 * mm, 85 * mm, 86 * mm, 53 * mm, fill=HexColor("#EAF4FF"), stroke=HexColor("#BBDDFC"))
    c.setFillColor(BLUE_DARK); c.setFont("SRV-Bold", 12); c.drawString(20 * mm, 124 * mm, "管理员单机")
    text_block(c, "一台电脑完成接待、维修、库存、财务与备份，适合独立门店或起步阶段。", 20 * mm, 113 * mm, 70 * mm, size=8.3, leading=12, color=INK_2)
    card(c, 106 * mm, 85 * mm, 86 * mm, 53 * mm, fill=HexColor("#ECFAF4"), stroke=HexColor("#BFE8D6"))
    c.setFillColor(GREEN); c.setFont("SRV-Bold", 12); c.drawString(114 * mm, 124 * mm, "局域网协作")
    text_block(c, "数据库留在管理员主机，成员通过浏览器和 HTTP API 协作，不直接共享数据库文件。", 114 * mm, 113 * mm, 70 * mm, size=8.3, leading=12, color=INK_2)
    card(c, 200 * mm, 85 * mm, 85 * mm, 53 * mm, fill=HexColor("#F5F0FF"), stroke=HexColor("#D8C8F5"))
    c.setFillColor(HexColor("#7655B4")); c.setFont("SRV-Bold", 12); c.drawString(208 * mm, 124 * mm, "成长型部署")
    text_block(c, "高并发、多门店或公网访问场景，增加 PostgreSQL、HTTPS、反向代理和集中备份。", 208 * mm, 113 * mm, 69 * mm, size=8.3, leading=12, color=INK_2)
    values = [("减少重复录入", "客户、设备、工单和报价一次提交"), ("降低遗漏", "待办、SLA、回访和低库存集中提醒"), ("成本更可见", "采购成本、领料与实际收支进入毛利"), ("交付可追溯", "沟通、报告、物流与审计统一留痕")]
    for i, (title, body) in enumerate(values):
        x = 12 * mm + i * 69 * mm
        c.setFillColor(INK); c.setFont("SRV-Bold", 10); c.drawString(x, 63 * mm, title)
        text_block(c, body, x, 54 * mm, 61 * mm, size=7.8, leading=11, color=MUTED, max_lines=3)
    c.showPage()

    # 08 close
    c.setFillColor(SIDEBAR); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(BLUE_DARK); c.circle(w - 25 * mm, 15 * mm, 80 * mm, fill=1, stroke=0)
    c.setFillColor(Color(0.09, 0.57, 1, alpha=0.25)); c.circle(w - 58 * mm, 55 * mm, 105 * mm, fill=1, stroke=0)
    c.setFillColor(CYAN); c.setFont("SRV-Bold", 8); c.drawString(16 * mm, h - 35 * mm, "DIGITAL SERVICE WORKBENCH")
    c.setFillColor(white); c.setFont("SRV-Bold", 30); c.drawString(16 * mm, h - 58 * mm, "让每一次服务，")
    c.drawString(16 * mm, h - 76 * mm, "都有清晰的下一步")
    text_block(c, "适合数码维修、无人机服务、售后与零售协同场景。部署、演示与角色配置请联系系统管理员或项目交付方。", 16 * mm, h - 94 * mm, 135 * mm, size=11, leading=17, color=HexColor("#C8D0DE"))
    card(c, 174 * mm, 54 * mm, 105 * mm, 81 * mm, fill=HexColor("#242731"), stroke=HexColor("#343846"), radius=7 * mm)
    draw_screenshot_fit(c, TMP / "dashboard.png", 180 * mm, 60 * mm, 93 * mm, 69 * mm)
    c.setFillColor(HexColor("#AEB7C7")); c.setFont("SRV-Regular", 7); c.drawString(16 * mm, 18 * mm, "本手册不替代正式部署评估与外部接口联调验收")
    c.save()


if __name__ == "__main__":
    make_manual()
    make_brochure()
    print(MANUAL_PATH)
    print(BROCHURE_PATH)
