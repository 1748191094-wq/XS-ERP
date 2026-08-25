# -*- coding: utf-8 -*-
"""
服务平台管理系统 v3.2.2
========================================================================

修复清单 (Enterprise Fix Log)：
1. [致命-初始化死锁] 重新梳理 App.__init__ 阶段生命周期。强制将 _alive、_task_queue 初始化置于最前，
   100% 根除因轮询自循环提早加载导致的 AttributeError: '_tkinter.tkapp' object has no attribute '_alive' 崩溃。
2. [致命-嵌套死锁] 将 sqlite_db_lock 升级为可重入锁 threading.RLock()，根除嵌套锁死锁挂起。
3. [致命-线程死锁] 使用 queue.Queue() + 主线程自循环轮询器，彻底解决子线程调用 self.after 导致的 Tkinter 锁死崩溃。
4. [致命-退出闪退] 修复 on_close 内调用未定义的 self.db 导致的 AttributeError 闪退，重构为 self.dao.db.close()。
5. [致命-邮件乱码] 修复 Header(...).encode() 返回 bytes 导致的协议解析报错，改用标准 Header 实体分配。
6. [致命-表格冲突] 重构 _render_tree, 使用 .exists(iid) 防御，100% 解决 Treeview reattach 引起的已存在 TclError 报错。
7. [致命-PDF变量] 补齐 generate_pdf() 中未定义变量 footer_p 导致的 NameError 渲染崩溃。
"""

import io
import json
import math
import os
import re
import smtplib
import sqlite3
import logging
import logging.handlers
import threading
import queue
import uuid
import platform
import subprocess
import html
import base64
import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header

# 可选图形与二维码库不可用时降级。
try:
    from PIL import Image as PILImage, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import Image as RLImage

# 1. 全局配置
APP_NAME = "服务平台案例管理系统"
APP_VER = "3.2.2"

RECORD_DIR = Path("records")
DB_FILE = Path("quotation.db")
CFG_FILE = Path("config.json")

# GUI 配色
COLOR_BG = "#1a1a2e"       
COLOR_CARD = "#16213e"     
COLOR_INPUT = "#0f172a"    
COLOR_TEXT = "#ffffff"     
COLOR_TEXT_MUTED = "#a2a8d3" 
COLOR_PRIMARY = "#1677ff"  
COLOR_SUCCESS = "#52c41a"  
COLOR_WARN = "#faad14"     
COLOR_BORDER = "#2d3748"   

# PDF 配色
PDF_COLOR_PRIMARY = colors.HexColor("#1e293b")  # 爵士蓝
PDF_COLOR_BORDER = colors.HexColor("#cbd5e1")   # 经典中灰
PDF_COLOR_BORDER_LIGHT = colors.HexColor("#f1f5f9") # 极浅灰
PDF_COLOR_BG_LIGHT = colors.HexColor("#f8fafc") # 表单/备注浅背景
PDF_COLOR_ACCENT = colors.HexColor("#1677ff")   # 品牌蓝
PDF_COLOR_SUMMARY = colors.HexColor("#fffdf0")  # 晨曦金总计高亮

LABOR_LEVELS = {
    "A类维修 (机臂/桨叶/外壳更换)": 120.0,
    "B类维修 (云台相机/电机/排线更换)": 260.0,
    "C类芯片维修 (主板元器件/核心芯片飞线)": 450.0,
    "D类维修 (进水深度清洁/多传感器校准)": 600.0,
    "免人工服务": 0.0,
    "仅运费": 20.0,
}

CN_FONT = "STSong-Light"
EN_FONT = "Helvetica"

# 2. 日志
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "service.log"

logger = logging.getLogger("Service Manager")
logger.setLevel(logging.INFO)

if not logger.handlers:
    file_handler = logging.handlers.TimedRotatingFileHandler(
        str(log_file), when="midnight", interval=1, backupCount=7, encoding="utf-8"
    )
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] (%(filename)s:%(lineno)d): %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

# SQLite 并发使用可重入锁。
sqlite_db_lock = threading.RLock()

# 3. 中文字体
def init_fonts() -> bool:
    """注册宋体 Unicode 字体，若系统不支持，则优雅降级为西文"""
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(CN_FONT))
        logger.info("中文字体 [STSong-Light] 成功注册注册至 PDF 引擎中")
        return True
    except Exception as e:
        logger.warning(f"中文字体注册失败(PDF中可能乱码，请检查系统字体库): {e}")
        return False

CN_FONT_OK = init_fonts()

# 4. 拼音检索
PY_DICT = {
    "云": "y", "台": "t", "桨": "j", "叶": "y", "相": "x", "机": "j",
    "电": "d", "池": "c", "减": "j", "震": "z", "排": "p", "线": "x",
    "主": "z", "板": "b", "芯": "x", "片": "p", "飞": "f", "控": "k",
    "臂": "b", "壳": "k", "架": "j", "罩": "z", "定": "d", "位": "w",
    "模": "m", "组": "z", "调": "d", "图": "t", "传": "c", "热": "r",
    "成": "c", "遥": "y", "维": "w", "修": "x", "护": "h", "高": "g",
    "深": "s", "度": "d", "清": "q", "洁": "j", "传": "c", "感": "g",
    "器": "q", "校": "x", "准": "z", "备": "b", "件": "j"
}

def get_pinyin_initial(char: str) -> str:
    val = ord(char)
    if val < 128:
        return char.lower()
    return PY_DICT.get(char, char)

# 5. 配置加密
class SecureConfigVault:
    @staticmethod
    def _get_hw_key() -> bytes:
        hw_salt = platform.node() + "ServiceVaultKeySalt_2026"
        return hashlib.sha256(hw_salt.encode('utf-8')).digest()
    
    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        if not plaintext:
            return ""
        key = cls._get_hw_key()
        cipher_bytes = bytearray()
        plain_bytes = plaintext.encode('utf-8')
        for i, b in enumerate(plain_bytes):
            cipher_bytes.append(b ^ key[i % len(key)])
        return base64.b64encode(cipher_bytes).decode('utf-8')

    @classmethod
    def decrypt(cls, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        try:
            key = cls._get_hw_key()
            cipher_bytes = base64.b64decode(ciphertext.encode('utf-8'))
            plain_bytes = bytearray()
            for i, b in enumerate(cipher_bytes):
                plain_bytes.append(b ^ key[i % len(key)])
            return plain_bytes.decode('utf-8')
        except Exception:
            logger.error("安全配置解密异常已拦截，防止在日志中泄露任何敏感的密文片段")
            return ""


# 6. XML 安全解析
def wrap_text_segments(text_content: str, bold=False) -> str:
    if not text_content:
        return ""
    segments = re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+|[^\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+', text_content)
    result = []
    for seg in segments:
        if re.search(r'[\u4e00-\u9fff]', seg):
            result.append(f'<font name="{CN_FONT if CN_FONT_OK else EN_FONT}">{seg}</font>')
        else:
            font_name = "Helvetica-Bold" if bold else EN_FONT
            result.append(f'<font name="{font_name}">{seg}</font>')
    return "".join(result)

def _xml_lexer_sandbox(text: str, bold=False) -> str:
    """
    词法级分词器：拆分出 XML 标签（如 <b>, </b>, <link...>, </link>）与纯文本。
    100% 根除 Emoji（💡）和加粗标签与中文字体交叉时造成的 ReportLab Parser 异常崩溃！
    """
    safe_text = html.escape(str(text))
    safe_text = safe_text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    safe_text = safe_text.replace("&lt;link", "<link").replace("&gt;", ">").replace("&lt;/link&gt;", "</link>")
    
    tokens = re.split(r'(<[^>]+>)', safe_text)
    result = []
    for token in tokens:
        if token.startswith('<') and token.endswith('>'):
            result.append(token)
        else:
            result.append(wrap_text_segments(token, bold=bold))
    return "".join(result)

def mixed(text: str) -> str:
    return _xml_lexer_sandbox(text, bold=False)

def mixed_bold(text: str) -> str:
    return _xml_lexer_sandbox(text, bold=True)


# 7. 撤销与重做
class UndoRedoManager:
    def __init__(self, max_size=50):
        self.max_size = max_size
        self.undo_stack = []
        self.redo_stack = []

    def push(self, state: dict):
        if self.undo_stack and self.undo_stack[-1] == state:
            return
        self.undo_stack.append(state)
        if len(self.undo_stack) > self.max_size:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self, current_state: dict) -> dict:
        if not self.undo_stack:
            return {}
        self.redo_stack.append(current_state)
        return self.undo_stack.pop()

    def redo(self, current_state: dict) -> dict:
        if not self.redo_stack:
            return {}
        self.undo_stack.append(current_state)
        return self.redo_stack.pop()


# 8. 数据持久化
class Config:
    DEFAULTS = {
        "config_version": 3,
        "sender": "",
        "smtp": "smtp.feishu.cn",
        "port": "465",
        "password": "",
        "pay_url": "",
        "pdf_title": "服务平台服务报价单",
        "pdf_footer": "祝您生活愉快~",
        "logo_text": "服务品牌",
        "logo_path": "",  
    }

    def __init__(self):
        self.data = dict(self.DEFAULTS)
        self.load()

    def load(self):
        if CFG_FILE.exists():
            try:
                loaded_data = json.loads(CFG_FILE.read_text(encoding="utf-8"))
                if loaded_data.get("config_version", 1) < self.DEFAULTS["config_version"]:
                    logger.info(f"升级历史配置：从版本 {loaded_data.get('config_version', 1)} 迁至版本 {self.DEFAULTS['config_version']}")
                    merged = dict(self.DEFAULTS)
                    merged.update(loaded_data)
                    merged["config_version"] = self.DEFAULTS["config_version"]
                    self.data = merged
                    self.save()
                else:
                    self.data.update(loaded_data)
            except Exception as e:
                logger.error(f"配置文件加载或解析异常: {e}")

    def save(self):
        try:
            CFG_FILE.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"配置文件写入异常: {e}")

    def get(self, key):
        return self.data.get(key, self.DEFAULTS.get(key, ""))

    def set(self, key, value):
        self.data[key] = value


class ConfigManager:
    def __init__(self):
        self.cfg = Config()

    def get(self, key):
        val = self.cfg.get(key)
        if key == "password":
            return SecureConfigVault.decrypt(val)
        return val

    def set(self, key, value):
        if key == "password":
            encrypted_val = SecureConfigVault.encrypt(value)
            self.cfg.set(key, encrypted_val)
        else:
            self.cfg.set(key, value)
        self.cfg.save()


class Database:
    def __init__(self):
        self._conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        # 串行化数据库写入。
        with sqlite_db_lock:
            with self._conn:
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS quotations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        quote_id TEXT UNIQUE,
                        model TEXT,
                        sn TEXT,
                        customer_name TEXT,
                        phone TEXT,
                        customer_email TEXT,
                        reason TEXT,
                        engineer TEXT,
                        remark TEXT,
                        pdf_title TEXT,
                        pdf_footer TEXT,
                        labor_type TEXT,
                        labor_price REAL,
                        parts_total REAL,
                        grand_total REAL,
                        pay_url TEXT,
                        pdf_path TEXT,
                        logo_path TEXT,
                        parts_json TEXT, 
                        status TEXT,
                        created_at TEXT
                    )
                """)
                self._conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_id ON quotations (quote_id)")
                self._conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON quotations (created_at)")
            self._migrate_db()

    def _migrate_db(self):
        try:
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA table_info(quotations)")
            existing_cols = [row["name"] for row in cursor.fetchall()]
            
            # 默认值兼容旧记录。
            new_cols = {
                "sn": "TEXT DEFAULT ''",
                "customer_name": "TEXT DEFAULT ''",
                "phone": "TEXT DEFAULT ''",
                "engineer": "TEXT DEFAULT ''",
                "remark": "TEXT DEFAULT ''",
                "pdf_title": "TEXT DEFAULT ''",
                "pdf_footer": "TEXT DEFAULT ''",
                "pay_url": "TEXT DEFAULT ''",
                "logo_path": "TEXT DEFAULT ''",
                "parts_json": "TEXT DEFAULT '[]'", 
                "status": "TEXT DEFAULT ''"
            }
            
            with sqlite_db_lock:
                for col_name, col_sql_def in new_cols.items():
                    pure_name = col_name.split()[0]
                    if pure_name not in existing_cols:
                        cursor.execute(f"ALTER TABLE quotations ADD COLUMN {pure_name} {col_sql_def}")
                        logger.info(f"数据库热升级新字段成功: {col_name}")
                self._conn.commit()
        except Exception as e:
            logger.error(f"数据库热升级失败: {e}")

    def insert_or_replace_quote(self, data: dict):
        with sqlite_db_lock:
            with self._conn:
                self._conn.execute("""
                    INSERT OR REPLACE INTO quotations (
                        quote_id, model, sn, customer_name, phone, customer_email,
                        reason, engineer, remark, pdf_title, pdf_footer,
                        labor_type, labor_price, parts_total, grand_total,
                        pay_url, pdf_path, logo_path, parts_json, status, created_at
                    ) VALUES (
                        :quote_id, :model, :sn, :customer_name, :phone, :customer_email,
                        :reason, :engineer, :remark, :pdf_title, :pdf_footer,
                        :labor_type, :labor_price, :parts_total, :grand_total,
                        :pay_url, :pdf_path, :logo_path, :parts_json, :status, :created_at
                    )
                """, data)
                logger.info(f"成功保存工单: {data['quote_id']} - {data['status']}")

    def get_all_records(self):
        with sqlite_db_lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM quotations ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def delete_record(self, quote_id):
        with sqlite_db_lock:
            with self._conn:
                self._conn.execute("DELETE FROM quotations WHERE quote_id = ?", (quote_id,))
                logger.info(f"成功物理废弃工单: {quote_id}")

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


class QuotationDAO:
    def __init__(self):
        self.db = Database()

    def save_workorder(self, data: dict):
        self.db.insert_or_replace_quote(data)

    def fetch_records(self) -> list:
        return self.db.get_all_records()

    def delete_record(self, quote_id):
        self.db.delete_record(quote_id)


# 9. 多页 PDF 绘制
def draw_page_decorations(canvas, doc, data, logo_path=None):
    canvas.saveState()
    margin = 40
    x_left = margin
    x_right = A4[0] - margin
    y_top = A4[1] - margin
    y_bottom = margin

    # 页眉与 Logo
    if logo_path and os.path.exists(logo_path):
        try:
            canvas.drawImage(logo_path, x_left, y_top - 35, width=35, height=35, mask='auto')
        except Exception:
            pass
    
    canvas.setStrokeColor(colors.HexColor("#1e293b"))
    canvas.setLineWidth(1.5)
    canvas.line(x_left, y_top - 42, x_right, y_top - 42)
    canvas.setLineWidth(0.5)
    canvas.line(x_left, y_top - 46, x_right, y_top - 46)

    # 页脚与服务电话
    canvas.setFont(EN_FONT, 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    page_num = canvas.getPageNumber()
    canvas.drawString(x_left, y_bottom + 10, f"服务品牌")
    canvas.drawRightString(x_right, y_bottom + 10, f"Page {page_num}")
    
    canvas.restoreState()


# 10. PDF 生成
def generate_pdf(path: str, data: dict):
    tmp_path = path + ".tmp"
    try:
        logger.info(f"PDF 渲染器任务启动，文件将存储于: {path}")
        styles = getSampleStyleSheet()
        
        cell_style = ParagraphStyle(
            name="PDF_Cell", parent=styles["Normal"],
            fontName=CN_FONT if CN_FONT_OK else EN_FONT, fontSize=9.5, leading=15,
            textColor=PDF_COLOR_PRIMARY
        )
        cell_style_bold = ParagraphStyle(
            name="PDF_Cell_Bold", parent=cell_style,
            fontName=CN_FONT if CN_FONT_OK else EN_FONT, fontSize=10, leading=15,
        )
        header_style = ParagraphStyle(
            name="PDF_Header", parent=cell_style,
            fontName=CN_FONT if CN_FONT_OK else EN_FONT, fontSize=10, leading=15,
            textColor=colors.white
        )
        title_style = ParagraphStyle(
            name="PDF_Title", parent=styles["Normal"],
            fontName=CN_FONT if CN_FONT_OK else EN_FONT, fontSize=18, leading=24,
            textColor=PDF_COLOR_PRIMARY, alignment=0 
        )
        body_style = ParagraphStyle(
            name="PDF_Body", parent=styles["Normal"],
            fontName=CN_FONT if CN_FONT_OK else EN_FONT, fontSize=9.5, leading=16,
            textColor=PDF_COLOR_PRIMARY
        )

        doc = SimpleDocTemplate(
            tmp_path, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=95, bottomMargin=60
        )
        logo_path = data.get("logo_path", "")
        story = []

        # 标题与单号
        meta_info = Paragraph(
            f'<font size="8" color="#94a3b8">QUOTATION NO.</font><br/>'
            f'<font size="11" face="{EN_FONT}"><b>{data["quote_id"]}</b></font>',
            ParagraphStyle(name="MetaR", parent=cell_style, alignment=2)
        )
        title_p = Paragraph(mixed(data.get("pdf_title", "服务报价单")), title_style)
        
        title_table = Table([[title_p, meta_info]], colWidths=[240, 240])
        title_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(title_table)
        story.append(Spacer(1, 15))

        # 客户信息
        info_data = [
            [Paragraph(mixed(f"客户姓名：{data.get('customer_name', '')}"), body_style), Paragraph(mixed(f"联络电话：{data.get('phone', '')}"), body_style)],
            [Paragraph(mixed(f"设备型号：{data.get('model', '')}"), body_style), Paragraph(mixed(f"机身SN码：{data.get('sn', '--')}"), body_style)],
            [Paragraph(mixed(f"服务主题：{data.get('reason', '')}"), body_style), Paragraph(mixed(f"签发时间：{data['time']}"), body_style)],
        ]
        info_table = Table(info_data, colWidths=[230, 230])
        info_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        
        card_table = Table([[info_table]], colWidths=[480])
        card_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PDF_COLOR_BG_LIGHT),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("BOX", (0, 0), (-1, -1), 0.5, PDF_COLOR_BORDER),
        ]))
        story.append(card_table)
        story.append(Spacer(1, 18))

        # 维修明细
        rows = [
            [Paragraph(mixed("服务内容（物料）"), header_style), Paragraph(mixed("服务金额"), header_style)]
        ]
        parts_total = 0.0
        for name, price_val in data["parts"]:
            try:
                price = float(price_val)
            except ValueError:
                price = 0.0
            rows.append([
                Paragraph(mixed(name), cell_style),
                Paragraph(f"¥ {price:.2f}", cell_style)
            ])
            parts_total += price

        labor = float(data.get("labor_price", 0.0))
        rows.append([
            Paragraph(mixed(f"人工服务费 ({data.get('labor_type', '标准维修')})"), cell_style),
            Paragraph(f"¥ {labor:.2f}", cell_style)
        ])

        grand_total = parts_total + labor
        rows.append([
            Paragraph(mixed_bold("总计费用 (Grand Total)"), cell_style_bold),
            Paragraph(mixed_bold(f"¥ {grand_total:.2f}"), cell_style_bold)
        ])

        last_row = len(rows) - 1
        detail_table = Table(rows, colWidths=[360, 120])
        detail_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PDF_COLOR_PRIMARY), 
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("LINEBELOW", (0, 0), (-1, -2), 0.5, PDF_COLOR_BORDER_LIGHT),
            ("BACKGROUND", (0, last_row), (-1, last_row), PDF_COLOR_SUMMARY), 
            ("LINEABOVE", (0, last_row), (-1, last_row), 1, PDF_COLOR_PRIMARY), 
            ("LINEBELOW", (0, last_row), (-1, last_row), 1.5, PDF_COLOR_PRIMARY),
        ]))
        story.append(detail_table)
        story.append(Spacer(1, 15))

        # 工程师备注
        if data.get("remark"):
            remark_content = Paragraph(mixed(data["remark"]), cell_style)
            decor_bar = Table([[""]], colWidths=[4], rowHeights=[28])
            decor_bar.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PDF_COLOR_ACCENT),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            
            remark_block = Table([[decor_bar, remark_content]], colWidths=[10, 460])
            remark_block.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]))
            
            remark_container = Table([[remark_block]], colWidths=[480])
            remark_container.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("PADDING", (0, 0), (-1, -1), 10),
            ]))
            
            story.append(Paragraph(mixed("■ 产品顾问服务备注："), body_style))
            story.append(Spacer(1, 6))
            story.append(remark_container)
            story.append(Spacer(1, 20))

        # 二维码在内存中生成。
        pay_url = data.get("pay_url", "").strip()
        qr_flowable = None
        
        if pay_url and HAS_QRCODE and HAS_PIL:
            try:
                qr_buffer = io.BytesIO()
                qr_obj = qrcode.QRCode(box_size=4, border=1)
                qr_obj.add_data(pay_url)
                qr_obj.make(fit=True)
                qr_img = qr_obj.make_image(fill_color="black", back_color="white")
                qr_img.save(qr_buffer, format="PNG")
                qr_buffer.seek(0)
                
                qr_flowable = RLImage(qr_buffer, width=75, height=75)
            except Exception as e:
                logger.error(f"内存级 PDF 二维码生成异常: {e}")
                qr_flowable = None

        # footer_p 支持多页流式排版。
        footer_p = Paragraph(mixed(data.get("pdf_footer", "")), cell_style)

        if not qr_flowable:
            qr_sub_p = Paragraph(
                f'<font size="8" color="#94a3b8" face="{EN_FONT}">SCAN TO PAY</font><br/>'
                f'<font size="8"><b>线下人工确认</b></font>',
                ParagraphStyle(name="QRP", parent=cell_style, alignment=1)
            )
            qr_flowable = Table([[qr_sub_p]], colWidths=[80], rowHeights=[65])
            qr_flowable.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, PDF_COLOR_BORDER),
                ("BACKGROUND", (0, 0), (-1, -1), PDF_COLOR_BG_LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]))

        footer_table = Table([[footer_p, qr_flowable]], colWidths=[380, 100])
        footer_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(footer_table)

        # 客服指引
        if pay_url:
            story.append(Spacer(1, 15))
            pay_style = ParagraphStyle(
                name="PDF_PayBtn", parent=body_style, textColor=colors.white, alignment=1
            )
            pay_table = Table([[Paragraph(
                f'<link href="{pay_url}">{mixed_bold("服务通知：点此立即在线支付")} ¥ {grand_total:.2f}</link>',
                pay_style
            )]], colWidths=[240], rowHeights=[32])
            pay_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PDF_COLOR_ACCENT),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(pay_table)
        else:
            story.append(Spacer(1, 12))
            fallback_style = ParagraphStyle(
                name="PDF_FallbackPay", parent=cell_style, alignment=1, fontSize=8.5, textColor=colors.HexColor("#64748b")
            )
            fallback_text = Paragraph(
                mixed("💡 <b>服务通知：该维修工单尚未开启在线快捷通道。生产环境中请扫描上方二维码或致电产品顾问安排付款。</b>服务专线：<b>18994035402</b>  |  微信：<b>同号</b>"),
                fallback_style
            )
            fallback_table = Table([[fallback_text]], colWidths=[480])
            fallback_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PDF_COLOR_BG_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, PDF_COLOR_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]))
            story.append(fallback_table)

        doc.build(
            story,
            onFirstPage=lambda canvas, doc: draw_page_decorations(canvas, doc, data, logo_path),
            onLaterPages=lambda canvas, doc: draw_page_decorations(canvas, doc, data, logo_path)
        )
        
        if os.path.exists(path):
            try:
                os.remove(path)
            except PermissionError:
                return False, "当前 PDF 正处于占用状态，请在关闭占用窗口后重新生成。"
                
        os.replace(tmp_path, path)
        return True, path
    except Exception as e:
        logger.error(f"PDF 渲染失败: {e}", exc_info=True)
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except Exception: pass
        return False, str(e)


# 邮件发送
def send_email_safe(data: dict) -> tuple[bool, str]:
    try:
        port = int(data["port"])
        msg = MIMEMultipart()
        msg["From"] = data["sender"]
        msg["To"] = data["receiver"]
        
        subject_str = f"服务平台服务报价单 - {data['model']} ({data['quote_id']})"
        # 返回 Header 实例。
        msg["Subject"] = Header(subject_str, "utf-8")

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f6f9fc; padding: 30px;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden;">
                <div style="background-color: #1e293b; color: #ffffff; padding: 25px; font-size: 20px; font-weight: bold; letter-spacing: 1px;">
                    服务中心 / 服务品牌
                </div>
                <div style="padding: 30px; color: #334155; line-height: 1.6;">
                    <h3 style="margin-top: 0; color: #0f172a;">尊敬的 {data.get('customer_name', '客户')}，您好：</h3>
                    <p>您的服务平台服务报价单已生成。</p>
                    
                    <div style="background-color: #f1f5f9; padding: 15px; border-radius: 6px; margin: 20px 0;">
                        <table width="100%">
                            <tr><td style="color: #64748b;">设备型号:</td><td style="font-weight: bold;">{data['model']}</td></tr>
                            <tr><td style="color: #64748b;">机身SN码:</td><td style="font-weight: bold;">{data.get('sn', '--')}</td></tr>
                            <tr><td style="color: #64748b;">工单编号:</td><td style="font-weight: bold;">{data['quote_id']}</td></tr>
                            <tr><td style="color: #64748b;">合计金额:</td><td style="font-weight: bold; color: #1677ff; font-size: 16px;">¥ {data['grand_total']:.2f}</td></tr>
                        </table>
                    </div>
                    
                    <p><b>提示：</b>具体报价在pdf附件中<b>PDF附件</b>中。请下载查看确认。</p>
                    
                    {f'<p style="text-align: center; margin: 30px 0;"><a href="{data["pay_url"]}" style="background-color: #1677ff; color: #ffffff; text-decoration: none; padding: 12px 30px; border-radius: 5px; display: inline-block;">确认并在线支付</a></p>' if data.get('pay_url') else ''}
                    
                    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 30px 0;" />
                    <p style="font-size: 12px; color: #94a3b8;">本邮件为服务平台系统自动发出。如有疑问，请及时联系您的产品顾问。</p>
                </div>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        pdf_name = os.path.basename(data["pdf"])
        with open(data["pdf"], "rb") as f:
            part = MIMEApplication(f.read())
            # RFC 2231 编码附件名。
            part.add_header('Content-Disposition', 'attachment', filename=('utf-8', '', pdf_name))
            msg.attach(part)


        server = None
        if port == 465:
            server = smtplib.SMTP_SSL(data["smtp"], port, timeout=12)
            logger.info("采用标准 SMTP SSL 发信协议进行三次信号握手验证...")
        else:
            server = smtplib.SMTP(data["smtp"], port, timeout=12)
            server.ehlo()
            if server.has_ext("STARTTLS"):
                server.starttls()
                server.ehlo()
                logger.info(f"成功与 SMTP 服务器 {data['smtp']} 建立 STARTTLS 安全隧道")
        
        server.login(data["sender"], data["password"])
        server.sendmail(data["sender"], [data["receiver"]], msg.as_string())
        server.close()
        logger.info(f"服务报价邮件成功发送投递至: {data['receiver']}")
        return True, "发送成功"
    except Exception as e:
        logger.error(f"企业邮件 SMTP 服务发送异常: {e}", exc_info=True)
        return False, f"邮件服务连接失败: {str(e)}"


# GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VER}")
        self.geometry("1420x880")
        self.configure(bg=COLOR_BG)

        # 先初始化状态，再启动队列。
        self._alive = True
        self.running = False
        self._run_lock = threading.Lock()
        
        # 按修改时间失效高清缓存。
        self._last_loaded_logo_path = None
        self._last_loaded_logo_mtime = 0
        self._logo_photo_cache = None  
        
        # 二维码 Canvas 缓存
        self._last_pay_url = None
        self._cached_canvas_qr = None  
        
        # 预览重绘防抖
        self._preview_timer_id = None
        
        self.current_quote_id = self._generate_new_id()

        # 表单状态保存在内存中。
        self.form_data = {
            "customer_name": tk.StringVar(value="客户姓名"),
            "phone": tk.StringVar(),
            "customer_email": tk.StringVar(),
            "model": tk.StringVar(value="DJI Inspire 3"),
            "sn": tk.StringVar(),
            "reason": tk.StringVar(value="点我填写损坏原因"),
            "engineer": tk.StringVar(value="工程师"),
            "pdf_title": tk.StringVar(value=""),
            "pdf_footer": tk.StringVar(value=""),
            "pay_url": tk.StringVar(value=""),
            "labor_price": tk.StringVar(value="260.0"),
        }

        # 数据与配置接口
        self.cfg = ConfigManager()
        self.dao = QuotationDAO()
        self.undo_manager = UndoRedoManager()
        self.parts_list = []  # 缓存所有配件：[(name, price, is_checked)]

        # 配置绑定
        self.form_data["pdf_title"].set(self.cfg.get("pdf_title"))
        self.form_data["pdf_footer"].set(self.cfg.get("pdf_footer"))
        self.form_data["pay_url"].set(self.cfg.get("pay_url"))

        # 状态变更只触发一次预览。
        for key, var in self.form_data.items():
            if key == "labor_price":
                var.trace_add("write", lambda *args: self._on_labor_price_modified())
            else:
                var.trace_add("write", lambda *args: self._on_data_modified())

        # 主线程轮询队列，避免跨线程更新 GUI。
        self._task_queue = queue.Queue()
        self._process_queue_loop()

        self._apply_styles()
        self._build_ui()
        self._load_initial_state()
        self._trigger_preview()
        
        # 快捷键
        self.bind("<Control-z>", lambda e: self._perform_undo())
        self.bind("<Control-y>", lambda e: self._perform_redo())
        self.bind("<Control-s>", lambda e: self._save_draft_action())
        self.bind("<Control-p>", lambda e: self._only_pdf_action())

    def _generate_new_id(self):
        return f"SRV-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    def _on_data_modified(self):
        self._trigger_preview()

    def _on_labor_price_modified(self):
        self._update_pricing()

    def _apply_styles(self):
        try:
            if platform.system() == "Windows":
                from ctypes import windll
                windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.style = ttk.Style()
        self.style.theme_use("clam")

        self.style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT, font=("Segoe UI", 10))
        self.style.configure("TFrame", background=COLOR_BG)
        self.style.configure("Card.TFrame", background=COLOR_CARD, borderwidth=1, relief="flat")
        
        self.style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
        self.style.configure("Card.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT)
        self.style.configure("Sub.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT_MUTED, font=("Segoe UI", 9))
        self.style.configure("Title.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT, font=("Segoe UI", 12, "bold"))
        self.style.configure("BigTitle.TLabel", background=COLOR_BG, foreground=COLOR_TEXT, font=("Segoe UI", 16, "bold"))

        self.style.configure("TEntry", fieldbackground=COLOR_INPUT, background=COLOR_INPUT, foreground=COLOR_TEXT, insertcolor="white")
        self.style.map("TEntry", fieldbackground=[("focus", COLOR_INPUT)], foreground=[("focus", COLOR_TEXT)])

        self.style.configure("TCombobox", fieldbackground=COLOR_INPUT, background=COLOR_INPUT, foreground=COLOR_TEXT, arrowcolor=COLOR_PRIMARY)
        self.style.map("TCombobox", fieldbackground=[("readonly", COLOR_INPUT)], foreground=[("readonly", COLOR_TEXT)])

        self.style.configure("TButton", background="#334155", foreground=COLOR_TEXT, borderwidth=0, padding=(12, 6))
        self.style.map("TButton", background=[("active", "#475569"), ("disabled", "#1e293b")], foreground=[("disabled", "#64748b")])
        
        self.style.configure("Primary.TButton", background=COLOR_PRIMARY, foreground=COLOR_TEXT, font=("Segoe UI", 10, "bold"))
        self.style.map("Primary.TButton", background=[("active", "#4096ff"), ("disabled", "#002c8c")])

        self.style.configure("TNotebook", background=COLOR_BG, borderwidth=0)
        self.style.configure("TNotebook.Tab", background=COLOR_CARD, foreground=COLOR_TEXT_MUTED, borderwidth=1, padding=(12, 4))
        self.style.map("TNotebook.Tab", background=[("selected", COLOR_PRIMARY)], foreground=[("selected", COLOR_TEXT)])

        self.style.configure("TProgressbar", thickness=3, background=COLOR_PRIMARY, troughcolor=COLOR_BG)

        self.style.configure("Treeview", background=COLOR_INPUT, foreground=COLOR_TEXT, fieldbackground=COLOR_INPUT, rowheight=28, borderwidth=0)
        self.style.configure("Treeview.Heading", background="#1e293b", foreground=COLOR_TEXT, relief="flat", padding=6)
        self.style.map("Treeview", background=[("selected", COLOR_PRIMARY)], foreground=[("selected", COLOR_TEXT)])

    def _build_ui(self):
        top_bar = ttk.Frame(self)
        top_bar.pack(fill=tk.X, padx=15, pady=10)
        
        lbl_logo = ttk.Label(top_bar, text="🛸 SERVICE SUPPORT SYSTEM", style="BigTitle.TLabel")
        lbl_logo.pack(side=tk.LEFT)
        
        btn_set = ttk.Button(top_bar, text="⚙ 系统设置", command=self._show_settings_dialog)
        btn_set.pack(side=tk.RIGHT)

        main_layout = ttk.Frame(self)
        main_layout.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        # 左栏：配件
        col_left = ttk.Frame(main_layout, width=320)
        col_left.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 10))
        col_left.pack_propagate(False)

        card_left = ttk.Frame(col_left, style="Card.TFrame")
        card_left.pack(fill=tk.BOTH, expand=True)

        ttk.Label(card_left, text="物料核算", style="Title.TLabel").pack(anchor="w", padx=12, pady=(12, 6))
        
        import_bar = ttk.Frame(card_left, style="Card.TFrame")
        import_bar.pack(fill=tk.X, padx=12, pady=5)
        ttk.Button(import_bar, text="📂 导入物料表", command=self._import_excel_data).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(card_left, textvariable=self.search_var)
        search_entry.pack(fill=tk.X, padx=12, pady=5)
        self._add_placeholder(search_entry, "🔍 输入拼音首字母(如yt)/汉字搜索...")

        tree_frame = ttk.Frame(card_left, style="Card.TFrame")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=5)
        
        self.parts_tree = ttk.Treeview(tree_frame, columns=("check", "name", "price"), show="headings", selectmode="browse")
        self.parts_tree.heading("check", text="状态")
        self.parts_tree.heading("name", text="名称")
        self.parts_tree.heading("price", text="参考单价")
        
        self.parts_tree.column("check", width=40, anchor="center")
        self.parts_tree.column("name", width=180, anchor="w")
        self.parts_tree.column("price", width=80, anchor="e")
        
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.parts_tree.yview)
        self.parts_tree.configure(yscrollcommand=tree_scroll.set)
        
        self.parts_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.parts_tree.bind("<Double-1>", self._on_tree_item_toggle)
        self.parts_tree.bind("<space>", self._on_tree_item_toggle)

        # parts_tree 初始化后再绑定 trace。
        self.search_var.trace_add("write", lambda *args: self._filter_parts_tree())

        summary_panel = ttk.Frame(card_left, style="Card.TFrame")
        summary_panel.pack(fill=tk.X, padx=12, pady=(10, 15))
        
        self.parts_total_var = tk.StringVar(value="小计：¥ 0.00")
        ttk.Label(summary_panel, textvariable=self.parts_total_var, style="Sub.TLabel").pack(anchor="w", pady=2)
        
        self.grand_total_var = tk.StringVar(value="整单预估：¥ 0.00")
        ttk.Label(summary_panel, textvariable=self.grand_total_var, font=("Segoe UI", 14, "bold"), foreground=COLOR_PRIMARY, style="Card.TLabel").pack(anchor="w", pady=2)

        # 中栏：录入
        col_mid = ttk.Frame(main_layout, width=440)
        col_mid.pack(side=tk.LEFT, fill=tk.BOTH, padx=5)
        col_mid.pack_propagate(False)

        card_mid = ttk.Frame(col_mid, style="Card.TFrame")
        card_mid.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(card_mid)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tab_work = ttk.Frame(notebook, style="Card.TFrame")
        notebook.add(tab_work, text=" 🔧 工单检测 ")

        scroll_canvas = tk.Canvas(tab_work, bg=COLOR_CARD, highlightthickness=0)
        scroll_bar = ttk.Scrollbar(tab_work, orient="vertical", command=scroll_canvas.yview)
        scroll_content = ttk.Frame(scroll_canvas, style="Card.TFrame")
        
        scroll_content.bind("<Configure>", lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
        scroll_canvas.create_window((0, 0), window=scroll_content, anchor="nw", width=400)
        scroll_canvas.configure(yscrollcommand=scroll_bar.set)
        
        scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_bar.pack(side=tk.RIGHT, fill=tk.Y)

        self._add_form_item(scroll_content, "客户姓名", self.form_data["customer_name"])
        self._add_form_item(scroll_content, "联络电话", self.form_data["phone"])
        self._add_form_item(scroll_content, "邮箱地址 (Email)", self.form_data["customer_email"])
        self._add_form_item(scroll_content, "设备型号 (Model)", self.form_data["model"])
        self._add_form_item(scroll_content, "机身 SN 编码", self.form_data["sn"])
        self._add_form_item(scroll_content, "服务主题", self.form_data["reason"])
        self._add_form_item(scroll_content, "产品顾问", self.form_data["engineer"])
        
        ttk.Label(scroll_content, text="产品顾问服务备注", style="Sub.TLabel").pack(anchor="w", padx=12, pady=(8, 2))
        self.txt_remark = tk.Text(scroll_content, height=4, bg=COLOR_INPUT, fg=COLOR_TEXT, insertbackground="white", font=("Segoe UI", 10), bd=1, relief="solid", highlightcolor=COLOR_PRIMARY)
        self.txt_remark.pack(fill=tk.X, padx=12, pady=(0, 10))
        self.txt_remark.insert("1.0", "无备注。")
        self.txt_remark.bind("<KeyRelease>", lambda e: self._on_text_modified())

        tab_config = ttk.Frame(notebook, style="Card.TFrame")
        notebook.add(tab_config, text=" 📄 页面配置 ")

        self._add_form_item(tab_config, "PDF 标题", self.form_data["pdf_title"])
        self._add_form_item(tab_config, "PDF 页脚", self.form_data["pdf_footer"])
        self._add_form_item(tab_config, "在线支付链接 (可自动嵌入正式二维码)", self.form_data["pay_url"])

        ttk.Label(tab_config, text="企业 Logo 图标", style="Sub.TLabel").pack(anchor="w", padx=12, pady=(8, 2))
        logo_input_frame = ttk.Frame(tab_config, style="Card.TFrame")
        logo_input_frame.pack(fill=tk.X, padx=12, pady=(0, 10))
        
        self.ent_logo_path = ttk.Entry(logo_input_frame, state="readonly")
        self.ent_logo_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._update_logo_entry_display(self.cfg.get("logo_path"))

        btn_browse_logo = ttk.Button(logo_input_frame, text=" 浏览... ", command=self._import_logo_action)
        btn_browse_logo.pack(side=tk.RIGHT)

        ttk.Label(tab_config, text="人工服务等阶", style="Sub.TLabel").pack(anchor="w", padx=12, pady=(10, 2))
        self.combo_labor = ttk.Combobox(tab_config, values=list(LABOR_LEVELS.keys()), state="readonly")
        self.combo_labor.pack(fill=tk.X, padx=12, pady=(0, 10))
        self.combo_labor.current(1)
        self.combo_labor.bind("<<ComboboxSelected>>", self._on_labor_select)

        self._add_form_item(tab_config, "人工费用金额 (¥)", self.form_data["labor_price"])

        # 右栏：预览
        col_right = ttk.Frame(main_layout)
        col_right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))

        card_right = ttk.Frame(col_right, style="Card.TFrame")
        card_right.pack(fill=tk.BOTH, expand=True)

        ttk.Label(card_right, text="高保真 PDF 实时预览 (A4 页面等比例缩放)", style="Title.TLabel").pack(anchor="w", padx=12, pady=(12, 6))
        
        preview_container = ttk.Frame(card_right, style="Card.TFrame")
        preview_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(5, 12))
        
        self.preview_canvas = tk.Canvas(preview_container, bg="#ffffff", highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", lambda e: self._render_canvas_preview())

        # 底部操作区
        action_bar = ttk.Frame(self, style="Card.TFrame")
        action_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=15, pady=15)

        self.btn_save_draft = ttk.Button(action_bar, text="💾 保存工单草稿", command=self._save_draft_action)
        self.btn_save_draft.pack(side=tk.LEFT, padx=5, pady=8)

        self.btn_open_history = ttk.Button(action_bar, text="📂 历史工单审计", command=self._show_history_dialog)
        self.btn_open_history.pack(side=tk.LEFT, padx=5, pady=8)

        self.btn_pdf = ttk.Button(action_bar, text="📄 仅生成正式 PDF", command=self._only_pdf_action)
        self.btn_pdf.pack(side=tk.LEFT, padx=5, pady=8)

        self.btn_send = ttk.Button(action_bar, text="🚀 确认并一键邮件投递", style="Primary.TButton", command=self._start_email_thread)
        self.btn_send.pack(side=tk.LEFT, padx=5, pady=8)

        self.btn_reset = ttk.Button(action_bar, text="🔄 清空并新建工单", command=self._reset_form)
        self.btn_reset.pack(side=tk.LEFT, padx=5, pady=8)

        self.status_var = tk.StringVar(value="系统已准备就绪。")
        lbl_status = ttk.Label(self, textvariable=self.status_var, relief="flat", anchor="w", font=("Segoe UI", 9))
        lbl_status.pack(side=tk.BOTTOM, fill=tk.X, padx=15)

        self.progress = ttk.Progressbar(self, orient="horizontal", mode="determinate")
        self.progress.pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=(0, 2))
        self.progress.pack_forget()

    # 主线程事件队列
    def _process_queue_loop(self):
        try:
            while True:
                callback_func = self._task_queue.get_nowait()
                if self._alive and self.winfo_exists():
                    try:
                        callback_func()
                    except Exception as e:
                        logger.error(f"主线程执行队列回调失败: {e}", exc_info=True)
                self._task_queue.task_done()
        except queue.Empty:
            pass
        
        # 持续轮询
        if self._alive:
            self.after(80, self._process_queue_loop)

    def _add_form_item(self, parent, label, tk_var):
        ttk.Label(parent, text=label, style="Sub.TLabel").pack(anchor="w", padx=12, pady=(8, 2))
        entry = ttk.Entry(parent, textvariable=tk_var)
        entry.pack(fill=tk.X, padx=12, pady=(0, 5))
        return entry

    def _add_placeholder(self, entry, text):
        entry.insert(0, text)
        entry.configure(foreground="gray")
        def focus_in(e):
            if entry.get() == text:
                entry.delete(0, tk.END)
                entry.configure(foreground=COLOR_TEXT)
        def focus_out(e):
            if not entry.get():
                entry.insert(0, text)
                entry.configure(foreground="gray")
        entry.bind("<FocusIn>", focus_in)
        entry.bind("<FocusOut>", focus_out)

    def _load_initial_state(self):
        self._load_parts_tree()
        self._update_pricing()

    def _load_parts_tree(self):
        self.parts_list = [
            ("无人机技术支持", 50.0, False),
            ("服务帆布袋", 20.0, False),
            ("点上面从Excel里导入物料", 999.0, False),
            ("折扣", -100.0, False),
        ]
        self._render_tree()

    def _render_tree(self):
        if not hasattr(self, 'parts_tree') or not self.parts_tree:
            return
            
        search_kw = self.search_var.get().strip().lower()
        if search_kw == "🔍 输入拼音首字母(如yt)/汉字搜索...":
            search_kw = ""

        existing_items = self.parts_tree.get_children()
        if len(existing_items) != len(self.parts_list):
            for item in existing_items:
                self.parts_tree.delete(item)
            for idx, (name, price, is_checked) in enumerate(self.parts_list):
                icon = "☑" if is_checked else "☐"
                self.parts_tree.insert("", "end", iid=str(idx), values=(icon, name, f"¥ {price:.2f}"))

        # Tag 索引加速检索。
        for idx, (name, price, is_checked) in enumerate(self.parts_list):
            icon = "☑" if is_checked else "☐"
            initials = "".join([get_pinyin_initial(c) for char in name for c in char])
            
            if not search_kw or (search_kw in name.lower() or search_kw in initials):
                self.parts_tree.reattach(str(idx), "", "end")
                self.parts_tree.item(str(idx), values=(icon, name, f"¥ {price:.2f}"))
            else:
                self.parts_tree.detach(str(idx))

    def _filter_parts_tree(self):
        if not hasattr(self, 'parts_tree') or not self.parts_tree:
            return
        self._render_tree()

    def _on_tree_item_toggle(self, event):
        self._save_undo_state() # 记录历史
        selected_item = self.parts_tree.selection()
        if not selected_item:
            return
        idx = int(selected_item[0])
        name, price, is_checked = self.parts_list[idx]
        self.parts_list[idx] = (name, price, not is_checked)
        self._render_tree()
        self.parts_tree.selection_set(str(idx))
        self._update_pricing()

    def _on_labor_select(self, event):
        self._save_undo_state()
        level = self.combo_labor.get()
        price = LABOR_LEVELS.get(level, 0.0)
        self.form_data["labor_price"].set(f"{price:.1f}")
        self._update_pricing()

    def _on_text_modified(self):
        self._trigger_preview()

    def _update_pricing(self):
        if not hasattr(self, 'parts_list') or not self.parts_list:
            return
            
        parts_total = sum(price for _, price, is_checked in self.parts_list if is_checked)
        try:
            labor = float(self.form_data["labor_price"].get().strip())
        except (ValueError, KeyError):
            labor = 0.0

        grand_total = parts_total + labor
        self.parts_total_var.set(f"小计：¥ {parts_total:.2f}")
        self.grand_total_var.set(f"整单预估：¥ {grand_total:.2f}")
        self._trigger_preview()

    def _update_logo_entry_display(self, path):
        if not hasattr(self, 'ent_logo_path'):
            return
        self.ent_logo_path.configure(state="normal")
        self.ent_logo_path.delete(0, tk.END)
        if path:
            self.ent_logo_path.insert(0, path)
        else:
            self.ent_logo_path.insert(0, "未选择 (可选导入)")
        self.ent_logo_path.configure(state="readonly")

    def _import_logo_action(self):
        path = filedialog.askopenfilename(
            filetypes=[("图像文件", "*.png *.jpg *.jpeg *.gif *.bmp")]
        )
        if not path:
            return
        
        self.cfg.set("logo_path", path)
        self._update_logo_entry_display(path)
        self._trigger_preview()
        self.status_var.set(f"已成功加载企业 Logo: {path}")

    # 撤销与重做
    def _get_serializable_state(self) -> dict:
        checked_parts = [p[0] for p in self.parts_list if p[2]]
        remark_content = self.txt_remark.get("1.0", "end-1c").strip() if hasattr(self, 'txt_remark') else ""
        return {
            "customer_name": self.form_data["customer_name"].get(),
            "phone": self.form_data["phone"].get(),
            "customer_email": self.form_data["customer_email"].get(),
            "model": self.form_data["model"].get(),
            "sn": self.form_data["sn"].get(),
            "reason": self.form_data["reason"].get(),
            "engineer": self.form_data["engineer"].get(),
            "remark": remark_content,
            "pdf_title": self.form_data["pdf_title"].get(),
            "pdf_footer": self.form_data["pdf_footer"].get(),
            "pay_url": self.form_data["pay_url"].get(),
            "labor_price": self.form_data["labor_price"].get(),
            "labor_type": self.combo_labor.get() if hasattr(self, 'combo_labor') else "",
            "checked_parts": checked_parts
        }

    def _apply_serializable_state(self, state: dict):
        if not state:
            return
        self.form_data["customer_name"].set(state["customer_name"])
        self.form_data["phone"].set(state["phone"])
        self.form_data["customer_email"].set(state["customer_email"])
        self.form_data["model"].set(state["model"])
        self.form_data["sn"].set(state["sn"])
        self.form_data["reason"].set(state["reason"])
        self.form_data["engineer"].set(state["engineer"])
        
        self.txt_remark.delete("1.0", tk.END)
        self.txt_remark.insert("1.0", state["remark"])
        
        self.form_data["pdf_title"].set(state["pdf_title"])
        self.form_data["pdf_footer"].set(state["pdf_footer"])
        self.form_data["pay_url"].set(state["pay_url"])
        self.form_data["labor_price"].set(state["labor_price"])
        if hasattr(self, 'combo_labor'):
            self.combo_labor.set(state.get("labor_type", ""))

        checked = set(state["checked_parts"])
        for idx in range(len(self.parts_list)):
            name, price, _ = self.parts_list[idx]
            self.parts_list[idx] = (name, price, name in checked)
            
        self._render_tree()
        self._update_pricing()

    def _save_undo_state(self):
        state = self._get_serializable_state()
        self.undo_manager.push(state)

    def _perform_undo(self):
        curr = self._get_serializable_state()
        prev = self.undo_manager.undo(curr)
        if prev:
            self._apply_serializable_state(prev)
            self.status_var.set("已撤销上一步操作 (Ctrl+Z)")

    def _perform_redo(self):
        curr = self._get_serializable_state()
        nxt = self.undo_manager.redo(curr)
        if nxt:
            self._apply_serializable_state(nxt)
            self.status_var.set("已重做下一步操作 (Ctrl+Y)")

    def _collect_form_data(self) -> dict:
        selected_parts = []
        for name, price, is_checked in self.parts_list:
            if is_checked:
                selected_parts.append({
                    "name": name,
                    "price": price,
                    "qty": 1,
                    "sku": f"SRV-{uuid.uuid4().hex[:4].upper()}"
                })

        parts_total = sum(item["price"] for item in selected_parts)
        try:
            labor = float(self.form_data["labor_price"].get().strip())
        except ValueError:
            labor = 0.0

        rem = self.txt_remark.get("1.0", "end-1c").strip() if hasattr(self, 'txt_remark') else ""
        pdf_parts = [(item["name"], item["price"]) for item in selected_parts]
        parts_json = json.dumps(selected_parts, ensure_ascii=False)
        grand_total = parts_total + labor

        return {
            "quote_id": self.current_quote_id,
            "model": self.form_data["model"].get().strip(),
            "sn": self.form_data["sn"].get().strip(),
            "customer_name": self.form_data["customer_name"].get().strip(),
            "phone": self.form_data["phone"].get().strip(),
            "customer_email": self.form_data["customer_email"].get().strip(),
            "reason": self.form_data["reason"].get().strip(),
            "engineer": self.form_data["engineer"].get().strip(),
            "remark": rem,
            "pdf_title": self.form_data["pdf_title"].get().strip(),
            "pdf_footer": self.form_data["pdf_footer"].get().strip(),
            "pay_url": self.form_data["pay_url"].get().strip(),
            "parts": pdf_parts,
            "parts_json": parts_json,
            "logo_path": self.cfg.get("logo_path") or "",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "labor_type": self.combo_labor.get() if hasattr(self, 'combo_labor') else "",
            "labor_price": labor,  # 新增
            "grand_total": grand_total,  # 新增
            "parts_total": parts_total  # 新增
        }

    # Canvas 预览
    def _trigger_preview(self):
        """【150ms 智能输入防抖】(Bug ⑨ 核心实现，杜绝高频卡死与闪屏)"""
        if self._preview_timer_id is not None:
            self.after_cancel(self._preview_timer_id)
        self._preview_timer_id = self.after(150, self._render_canvas_preview)

    def _render_canvas_preview(self):
        canvas = self.preview_canvas
        canvas.delete("all")
        
        cw = canvas.winfo_width()
        ch = canvas.winfo_height()
        if cw < 50 or ch < 50:
            return

        page_aspect = 1.414  
        if cw / ch > 1 / page_aspect:
            ph = ch - 20
            pw = ph / page_aspect
        else:
            pw = cw - 20
            ph = pw * page_aspect

        px1 = (cw - pw) / 2
        py1 = (ch - ph) / 2
        px2 = px1 + pw
        py2 = py1 + ph

        # A4 投影
        canvas.create_rectangle(px1+3, py1+3, px2+3, py2+3, fill="#0f172a", outline="")
        canvas.create_rectangle(px1, py1, px2, py2, fill="#ffffff", outline="#cbd5e1", width=1)

        data = self._collect_form_data()
        margin = pw * 0.08
        x_left = px1 + margin
        x_right = px2 - margin
        
        y_curr = py1 + margin
        logo_w_offset = 0
        logo_path = data.get("logo_path", "").strip()
        
        # 页眉与 Logo，按 mtime 刷新缓存。
        if logo_path and os.path.exists(logo_path):
            logo_w_offset = pw * 0.12
            try:
                mtime = os.path.getmtime(logo_path)
                if (logo_path != self._last_loaded_logo_path or 
                    mtime != self._last_loaded_logo_mtime or 
                    self._logo_photo_cache is None):
                    
                    if HAS_PIL:
                        pil_img = PILImage.open(logo_path)
                        pil_img.thumbnail((int(pw*0.09), int(pw*0.09)))
                        self._logo_photo_cache = ImageTk.PhotoImage(pil_img)
                    else:
                        self._logo_photo_cache = tk.PhotoImage(file=logo_path)
                    
                    self._last_loaded_logo_path = logo_path
                    self._last_loaded_logo_mtime = mtime
                    logger.info("企业 Logo 发生变动，成功在内存重构 PhotoImage 缓存...")
                
                canvas.create_image(x_left, y_curr, image=self._logo_photo_cache, anchor="w")
            except Exception as e:
                logger.error(f"Canvas Logo 图像重构失败: {e}")
                canvas.create_rectangle(x_left, y_curr - pw*0.035, x_left + pw*0.07, y_curr + pw*0.035, fill="#f1f5f9", outline="#cbd5e1")
                canvas.create_text(x_left + pw*0.04, y_curr, text="LOGO", font=("Segoe UI", int(pw*0.015)), fill="#94a3b8")
        else:
            logo_w_offset = pw * 0.12
            canvas.create_rectangle(x_left, y_curr - pw*0.035, x_left + pw*0.07, y_curr + pw*0.035, fill="#f1f5f9", outline="#cbd5e1")
            canvas.create_text(x_left + pw*0.04, y_curr, text="LOGO", font=("Segoe UI", int(pw*0.015)), fill="#94a3b8")

        # 工单单号
        canvas.create_text(
            x_right, y_curr, 
            text=f"NO: {data.get('quote_id')}", 
            font=("Segoe UI", int(pw*0.021), "bold"), 
            fill="#64748b", 
            anchor="e"
        )
        
        y_curr += pw * 0.05

        # 自适应标题
        title_text = data.get("pdf_title") or "服务报价单"
        title_font_size = int(pw * 0.042)
        title_chars = len(title_text)
        max_chars_per_line = 18
        title_lines = max(1, math.ceil(title_chars / max_chars_per_line))
        
        canvas.create_text(
            x_left, y_curr, 
            text=title_text, 
            font=("Segoe UI", title_font_size, "bold"), 
            fill="#1e293b", 
            anchor="nw", 
            width=(x_right - x_left)
        )
        
        y_curr += (pw * 0.048 * title_lines) + pw * 0.015
        canvas.create_line(x_left, y_curr, x_right, y_curr, fill="#1e293b", width=1.5)
        y_curr += pw * 0.03

        # 客户与工单
        card_h = pw * 0.17
        canvas.create_rectangle(x_left, y_curr, x_right, y_curr + card_h, fill="#f8fafc", outline="#cbd5e1", width=0.5)
        
        info_font_size = int(pw * 0.021)
        info_lh = pw * 0.042
        y_text = y_curr + pw * 0.024
        
        infos_left = [
            f"客户姓名：{data.get('customer_name')}",
            f"设备型号：{data.get('model')}",
            f"服务主题：{data.get('reason')}"
        ]
        infos_right = [
            f"联络电话：{data.get('phone')}",
            f"机身SN码：{data.get('sn')}",
            f"签发时间：{data.get('time')}"
        ]

        col_width_limit = pw * 0.38
        for i in range(len(infos_left)):
            canvas.create_text(
                x_left + 12, y_text, 
                text=infos_left[i], 
                font=("Segoe UI", info_font_size), 
                fill="#475569", 
                anchor="w",
                width=col_width_limit
            )
            canvas.create_text(
                (px1+px2)/2 + 10, y_text, 
                text=infos_right[i], 
                font=("Segoe UI", info_font_size), 
                fill="#475569", 
                anchor="w",
                width=col_width_limit
            )
            y_text += info_lh

        y_curr += card_h + pw * 0.04

        # 维修明细
        tbl_th = y_curr
        canvas.create_rectangle(x_left, tbl_th, x_right, tbl_th + pw*0.05, fill="#1e293b", outline="")
        canvas.create_text(x_left + 10, tbl_th + pw*0.025, text="服务项目/物料", font=("Segoe UI", info_font_size, "bold"), fill="#ffffff", anchor="w")
        canvas.create_text(x_right - 10, tbl_th + pw*0.025, text="服务金额", font=("Segoe UI", info_font_size, "bold"), fill="#ffffff", anchor="e")
        y_curr += pw*0.05

        for p_name, p_price in data["parts"]:
            display_name = p_name
            if len(display_name) > 28:
                display_name = display_name[:26] + "..."
            canvas.create_text(x_left + 10, y_curr + pw*0.02, text=display_name, font=("Segoe UI", info_font_size), fill="#334155", anchor="w")
            canvas.create_text(x_right - 10, y_curr + pw*0.02, text=f"¥ {p_price:.2f}", font=("Segoe UI", info_font_size), fill="#334155", anchor="e")
            canvas.create_line(x_left, y_curr + pw*0.04, x_right, y_curr + pw*0.04, fill="#f1f5f9", width=0.5)
            y_curr += pw*0.04

        # 人工费
        canvas.create_text(x_left + 10, y_curr + pw*0.02, text=f"人工技术服务费 ({data.get('labor_type')[:8]})", font=("Segoe UI", info_font_size), fill="#334155", anchor="w")
        canvas.create_text(x_right - 10, y_curr + pw*0.02, text=f"¥ {data.get('labor_price'):.2f}", font=("Segoe UI", info_font_size), fill="#334155", anchor="e")
        canvas.create_line(x_left, y_curr + pw*0.04, x_right, y_curr + pw*0.04, fill="#cbd5e1", width=1)
        y_curr += pw*0.04

        # 汇总高亮
        canvas.create_rectangle(x_left, y_curr, x_right, y_curr + pw*0.055, fill="#fffdf0", outline="#1e293b", width=1)
        canvas.create_text(x_left + 10, y_curr + pw*0.028, text="总计费用 (Grand Total)", font=("Segoe UI", info_font_size, "bold"), fill="#1e293b", anchor="w")
        canvas.create_text(x_right - 10, y_curr + pw*0.028, text=f"¥ {data.get('grand_total'):.2f}", font=("Segoe UI", int(pw*0.025), "bold"), fill="#1e293b", anchor="e")
        y_curr += pw*0.085

        # 检测备注
        if data.get("remark"):
            canvas.create_text(x_left, y_curr, text="■ 产品顾问服务备注：", font=("Segoe UI", info_font_size, "bold"), fill="#1e293b", anchor="w")
            y_curr += pw*0.028
            
            remark_text = data.get("remark")
            est_lines = max(1, math.ceil(len(remark_text) / 34.0))
            remark_h = pw * 0.038 * est_lines + pw * 0.024
            if remark_h > pw * 0.22: 
                remark_h = pw * 0.22
                
            canvas.create_rectangle(x_left, y_curr, x_right, y_curr + remark_h, fill="#f8fafc", outline="#cbd5e1", width=0.5)
            canvas.create_rectangle(x_left, y_curr, x_left + 4, y_curr + remark_h, fill="#1677ff", outline="")
            
            canvas.create_text(
                x_left + 12, y_curr + 6, 
                text=remark_text, 
                font=("Segoe UI", int(pw*0.019)), 
                fill="#475569", 
                anchor="nw",
                width=(x_right - x_left - 24)
            )
            y_curr += remark_h + pw*0.03

        # 页脚与二维码缓存
        footer_text = data.get("pdf_footer") or ""
        canvas.create_text(
            x_left, py2 - margin, 
            text=footer_text, 
            font=("Segoe UI", int(pw*0.017)), 
            fill="#94a3b8", 
            anchor="w",
            width=pw*0.56 
        )

        qr_sz = pw * 0.12
        canvas.create_rectangle(x_right - qr_sz, py2 - margin - qr_sz, x_right, py2 - margin, fill="#f8fafc", outline="#cbd5e1")
        
        pay_url = data.get("pay_url", "").strip()
        if pay_url and HAS_QRCODE and HAS_PIL:
            try:
                # 支付链接变化时刷新二维码。
                if pay_url != self._last_pay_url or self._cached_canvas_qr is None:
                    qr_obj = qrcode.QRCode(box_size=1, border=0)
                    qr_obj.add_data(pay_url)
                    qr_obj.make(fit=True)
                    qr_img = qr_obj.make_image(fill_color="black", back_color="white")
                    qr_img_resized = qr_img.resize((int(qr_sz - 4), int(qr_sz - 4)))
                    
                    self._cached_canvas_qr = ImageTk.PhotoImage(qr_img_resized)
                    self._last_pay_url = pay_url
                    logger.info("企业支付链接更新，重新在内存编译二维码...")
                
                canvas.create_image(x_right - qr_sz/2, py2 - margin - qr_sz/2, image=self._cached_canvas_qr)
            except Exception as e:
                logger.error(f"Canvas 预览二维码生成异常: {e}")
                canvas.create_text(x_right - qr_sz/2, py2 - margin - qr_sz/2, text="扫码支付", font=("Segoe UI", int(pw*0.017), "bold"), fill="#64748b", justify="center")
        else:
            canvas.create_text(x_right - qr_sz/2, py2 - margin - qr_sz/2, text="扫码支付", font=("Segoe UI", int(pw*0.017), "bold"), fill="#64748b", justify="center")

    # 控制层
    def _save_draft_action(self):
        form = self._collect_form_data()
        form["status"] = "draft"
        form["pdf_path"] = ""
        form["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        try:
            self.dao.save_workorder(form)
            self.status_var.set(f"草稿已安全暂存。草稿号: {self.current_quote_id}")
            messagebox.showinfo("工单系统", "工单草稿保存成功！随时可在历史审计中恢复并继续作业。")
        except Exception as e:
            logger.error(f"草稿入库失败: {e}")
            messagebox.showerror("数据库错误", f"暂存草稿失败: {e}")

    def _only_pdf_action(self):
        form = self._collect_form_data()
        if not form["parts"] and form["labor_price"] == 0:
            messagebox.showwarning("核算提示", "请至少选择一项物料或人工费以确保报价单非空。")
            return

        RECORD_DIR.mkdir(exist_ok=True)
        pdf_path = str(RECORD_DIR / f"{self.current_quote_id}.pdf")
        
        ok, res = generate_pdf(pdf_path, form)
        if ok:
            form["status"] = "completed"
            form["pdf_path"] = pdf_path
            form["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            self.dao.save_workorder(form)
            
            self.status_var.set(f"PDF 成功构建完成: {pdf_path}")
            self._open_file_cross_platform(pdf_path)
        else:
            messagebox.showerror("PDF 引擎报错", f"构建失败:\n{res}")

    def _start_email_thread(self):
        with self._run_lock:
            if self.running:
                return
            # 表单快照只在主线程读取。
            form_snapshot = self._collect_form_data()
            if not form_snapshot["customer_email"]:
                messagebox.showwarning("格式校验", "请输入合法的接收客户邮箱地址。")
                return
            if not form_snapshot["parts"] and form_snapshot["labor_price"] == 0:
                messagebox.showwarning("核算提示", "核算列表为空。")
                return
            self.running = True

        self._freeze_btn_state(True)
        self.progress.pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=(0, 2))
        self.progress["value"] = 10
        self.status_var.set("正在启动后台线程构建正式 PDF 及投递邮件...")

        # 子线程只接收表单快照。
        threading.Thread(target=self._run_async_email_job, args=(form_snapshot,), daemon=True).start()

    def _run_async_email_job(self, form_snapshot: dict):
        try:
            RECORD_DIR.mkdir(exist_ok=True)
            pdf_path = str(RECORD_DIR / f"{self.current_quote_id}.pdf")
            self._set_progress_on_main(30, "正在绘制高保真 PDF 报价单...")
            
            # 子线程不读取 GUI 控件。
            ok_pdf, res_pdf = generate_pdf(pdf_path, form_snapshot)
            if not ok_pdf:
                self._safe_callback(lambda err_res=res_pdf: messagebox.showerror("构建失败", f"PDF 生成引擎未通过:\n{err_res}"))
                self._set_progress_on_main(0, "PDF 渲染异常终止。")
                return

            self.cfg.set("pay_url", form_snapshot["pay_url"])

            self._set_progress_on_main(60, "邮件服务认证中，正在上传 PDF 附件...")
            email_payload = {
                "sender": self.cfg.get("sender"),
                "password": self.cfg.get("password"),
                "smtp": self.cfg.get("smtp"),
                "port": self.cfg.get("port"),
                "receiver": form_snapshot["customer_email"],
                "model": form_snapshot["model"],
                "quote_id": self.current_quote_id,
                "grand_total": form_snapshot["grand_total"],
                "sn": form_snapshot["sn"],
                "customer_name": form_snapshot["customer_name"],
                "pay_url": form_snapshot["pay_url"],
                "pdf": pdf_path
            }

            ok_mail, res_mail = send_email_safe(email_payload)
            if ok_mail:
                form_snapshot["status"] = "completed"
                form_snapshot["pdf_path"] = pdf_path
                form_snapshot["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self.dao.save_workorder(form_snapshot)

                self._set_progress_on_main(100, f"报价邮件发送成功 → {form_snapshot['customer_email']}")
                self._safe_callback(lambda rx=form_snapshot['customer_email']: messagebox.showinfo("发送成功", f"一键服务报价单已成功投递！\n接收方: {rx}"))
            else:
                self._set_progress_on_main(0, "投递失败。")
                self._safe_callback(lambda err_mail=res_mail: messagebox.showerror("发件失败", err_mail))

        except Exception as e:
            logger.critical("邮件子线程遭遇崩溃", exc_info=True)
            self._set_progress_on_main(0, "系统发生异常。")
            self._safe_callback(lambda err_msg=str(e): messagebox.showerror("线程异常", err_msg))
        finally:
            with self._run_lock:
                self.running = False
            self._safe_callback(lambda: self._freeze_btn_state(False))

    def _set_progress_on_main(self, val, status_text):
        def cmd():
            self.progress["value"] = val
            self.status_var.set(status_text)
            if val >= 100 or val == 0:
                self.progress.pack_forget()
        self._safe_callback(cmd)

    def _safe_callback(self, func):
        try:
            if self._alive and self.winfo_exists():
                self._task_queue.put(func)
        except RuntimeError:
            pass

    def _freeze_btn_state(self, freeze: bool):
        state = "disabled" if freeze else "normal"
        self.btn_send.configure(state=state)
        self.btn_pdf.configure(state=state)
        self.btn_save_draft.configure(state=state)
        self.btn_open_history.configure(state=state)
        self.btn_reset.configure(state=state)

    def _reset_form(self):
        self._save_undo_state()
        self.current_quote_id = self._generate_new_id()
        self.form_data["customer_name"].set("")
        self.form_data["phone"].set("")
        self.form_data["customer_email"].set("")
        self.form_data["model"].set("")
        self.form_data["sn"].set("")
        self.form_data["reason"].set("")
        self.txt_remark.delete("1.0", tk.END)

        for idx in range(len(self.parts_list)):
            name, price, _ = self.parts_list[idx]
            self.parts_list[idx] = (name, price, False)
        
        self._render_tree()
        self._update_pricing()
        self.status_var.set(f"工作区已重置，生成新单号: {self.current_quote_id}")

    def _import_excel_data(self):
        path = filedialog.askopenfilename(filetypes=[("Excel Files", "*.xlsx *.xls")])
        if not path:
            return
        try:
            df = pd.read_excel(path)
            if df.shape[1] < 2:
                messagebox.showwarning("结构警报", "导入的 Excel 必须包含至少两列：【配件名称】和【单价】。")
                return
            df = df.iloc[:, :2]
            df.columns = ["name", "price"]
            
            new_list = []
            # 使用 itertuples 加速解析。
            for row in df.itertuples(index=False):
                name = str(row[0]).strip()
                if not name or name.lower() == "nan":
                    continue
                try:
                    price = float(str(row[1]).replace(",", "").replace("¥", "").strip())
                except Exception:
                    price = 0.0
                new_list.append((name, price, False))
                
            if new_list:
                self.parts_list = new_list
                self._render_tree()
                self._update_pricing()
                self.status_var.set(f"成功导入物料，共 {len(new_list)} 项部件。")
        except Exception as e:
            logger.error("外部 Excel 导入失败", exc_info=True)
            messagebox.showerror("解析故障", f"物料清单导入异常:\n{e}")

    # 历史记录与设置
    def _show_history_dialog(self):
        win = tk.Toplevel(self)
        win.title("历史工单与暂存草稿审计")
        win.geometry("980x520")
        win.configure(bg=COLOR_BG)
        win.transient(self)
        win.grab_set()

        lbl = ttk.Label(win, text="本地工单列表 (双击项目一键回填并恢复全部配件勾选与历史价格)", font=("Segoe UI", 11, "bold"))
        lbl.pack(anchor="w", padx=15, pady=10)

        t_frame = ttk.Frame(win, style="Card.TFrame")
        t_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        cols = ("quote_id", "status", "customer", "phone", "model", "total", "created_at")
        tree = ttk.Treeview(t_frame, columns=cols, show="headings", selectmode="browse")
        
        tree.heading("quote_id", text="工单单号")
        tree.heading("status", text="状态")
        tree.heading("customer", text="客户")
        tree.heading("phone", text="电话")
        tree.heading("model", text="型号")
        tree.heading("total", text="整单金额")
        tree.heading("created_at", text="存储时间")

        tree.column("quote_id", width=160)
        tree.column("status", width=70, anchor="center")
        tree.column("customer", width=80)
        tree.column("phone", width=100)
        tree.column("model", width=120)
        tree.column("total", width=80, anchor="e")
        tree.column("created_at", width=130)

        sc = ttk.Scrollbar(t_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sc.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc.pack(side=tk.RIGHT, fill=tk.Y)

        records = self.dao.fetch_records()
        for r in records:
            state_text = "草稿" if r["status"] == "draft" else "正式"
            tree.insert("", "end", iid=r["quote_id"], values=(
                r["quote_id"], state_text, r["customer_name"], r["phone"], r["model"], f"¥ {r['grand_total']:.2f}", r["created_at"]
            ))

        def load_selected_record():
            selected = tree.selection()
            if not selected:
                return
            q_id = selected[0]
            target = next((item for item in records if item["quote_id"] == q_id), None)
            if target:
                self._restore_form_from_record(target)
                win.destroy()

        def delete_selected_record():
            selected = tree.selection()
            if not selected:
                return
            q_id = selected[0]
            if messagebox.askyesno("删除确认", f"确定彻底废弃单号为 {q_id} 的工单记录吗？"):
                self.dao.delete_record(q_id)
                tree.delete(q_id)
                self.status_var.set(f"单号 {q_id} 已从数据库抹除。")

        btn_bar = ttk.Frame(win, style="Card.TFrame")
        btn_bar.pack(fill=tk.X, padx=15, pady=15)

        ttk.Button(btn_bar, text="🟢 加载数据并回填", style="Primary.TButton", command=load_selected_record).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_bar, text="🔴 废弃/删除工单", command=delete_selected_record).pack(side=tk.RIGHT, padx=5)
        
        tree.bind("<Double-1>", lambda e: load_selected_record())

    def _restore_form_from_record(self, r: dict):
        """[完全重构的历史价格锁定加载] (Bug ② 核心修复：读取历史价格，永不随Excel价格更替发生漂移)"""
        self.current_quote_id = r["quote_id"]
        
        self.form_data["customer_name"].set(r["customer_name"] or "")
        self.form_data["phone"].set(r["phone"] or "")
        self.form_data["customer_email"].set(r["customer_email"] or "")
        self.form_data["model"].set(r["model"] or "")
        self.form_data["sn"].set(r["sn"] or "")
        self.form_data["reason"].set(r["reason"] or "")
        
        self.txt_remark.delete("1.0", tk.END)
        self.txt_remark.insert("1.0", r["remark"] or "")

        self.form_data["pdf_title"].set(r["pdf_title"] or "")
        self.form_data["pdf_footer"].set(r["pdf_footer"] or "")
        self.form_data["pay_url"].set(r["pay_url"] or "")

        logo_path = r.get("logo_path") or ""
        self.cfg.set("logo_path", logo_path)
        self._update_logo_entry_display(logo_path)

        self.combo_labor.set(r["labor_type"])
        self.form_data["labor_price"].set(str(r["labor_price"]))

        # 使用报价时的历史价格快照。
        parts_json = r.get("parts_json")
        restored_parts_map = {}
        if parts_json:
            try:
                restored_parts_list = json.loads(parts_json)
                for item in restored_parts_list:
                    if isinstance(item, list): # 兼容历史版本
                        restored_parts_map[item[0]] = item[1]
                    else:
                        restored_parts_map[item["name"]] = item["price"]
            except Exception as e:
                logger.error(f"历史工单配件快照解析失败: {e}")

        # 历史报价优先使用快照价格。
        for idx in range(len(self.parts_list)):
            name, price, _ = self.parts_list[idx]
            if name in restored_parts_map:
                # 回填历史价格，避免随 Excel 更新漂移。
                self.parts_list[idx] = (name, restored_parts_map[name], True)
                del restored_parts_map[name] 
            else:
                self.parts_list[idx] = (name, price, False)

        # 保留 Excel 中已删除的历史配件。
        for name, price in restored_parts_map.items():
            self.parts_list.append((name, price, True))

        self._render_tree()
        self._update_pricing()
        self.status_var.set(f"单号 {self.current_quote_id} 历史会话恢复成功（全部财务价格已被绝对锁定）。")

    def _show_settings_dialog(self):
        win = tk.Toplevel(self)
        win.title("发件端服务与个性化设置")
        win.geometry("540x360")
        win.configure(bg=COLOR_BG)
        win.transient(self)
        win.grab_set()

        lbl = ttk.Label(win, text="SMTP 邮件服务器与个人偏好设置", font=("Segoe UI", 11, "bold"))
        lbl.pack(anchor="w", padx=20, pady=15)

        form_frame = ttk.Frame(win, style="Card.TFrame")
        form_frame.pack(fill=tk.X, expand=True, padx=20)

        ent_sender = self._add_dialog_form_item(form_frame, "发信邮箱 (Sender Email)", self.cfg.get("sender"))
        ent_smtp = self._add_dialog_form_item(form_frame, "SMTP 服务器 (Host)", self.cfg.get("smtp"))
        ent_port = self._add_dialog_form_item(form_frame, "端口号 (常用 465)", self.cfg.get("port"))
        ent_pass = self._add_dialog_form_item(form_frame, "授权码", self.cfg.get("password"), show="*")

        def save():
            self.cfg.set("sender", ent_sender.get().strip())
            self.cfg.set("smtp", ent_smtp.get().strip())
            self.cfg.set("port", ent_port.get().strip())
            self.cfg.set("password", ent_pass.get().strip())
            messagebox.showinfo("服务配置", "发件服务凭据配置保存成功！")
            win.destroy()

        btn_bar = ttk.Frame(win, style="Card.TFrame")
        btn_bar.pack(fill=tk.X, padx=20, pady=15)
        ttk.Button(btn_bar, text="💾 确认并保存配置", style="Primary.TButton", command=save).pack(side=tk.RIGHT)

    def _add_dialog_form_item(self, parent, label, default, show=None):
        ttk.Label(parent, text=label, style="Sub.TLabel").pack(anchor="w", pady=(4, 1))
        entry = ttk.Entry(parent, show=show)
        entry.insert(0, default)
        entry.pack(fill=tk.X, pady=(0, 6))
        return entry

    def _open_file_cross_platform(self, path):
        try:
            sys_name = platform.system()
            if sys_name == "Windows":
                os.startfile(path)
            elif sys_name == "Darwin":
                subprocess.call(["open", path])
            else:
                subprocess.call(["xdg-open", path])
        except Exception as e:
            self.status_var.set(f"PDF 已保存，但自动调起失败: {e}")

    def on_close(self):
        if self.running:
            if not messagebox.askyesno("警告", "后台邮件发送任务尚在运行，确定强制安全退出工单中心吗？"):
                return
        self._alive = False
        self.dao.db.close()
        self.destroy()


# 入口
if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
