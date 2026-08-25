from __future__ import annotations

import html
import json
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import BASE_DIR, settings
from app.models.entities import Quote, RepairOrder, ServiceTicket, ServiceTicketNote


JAZZ_BLUE = colors.HexColor("#1D1D1F")
ACCENT_BLUE = colors.HexColor("#007AFF")
TEXT_BLUE = colors.HexColor("#1D1D1F")
MUTED_BLUE = colors.HexColor("#6E6E73")
BORDER = colors.HexColor("#D2D2D7")
LIGHT_BORDER = colors.HexColor("#E5E5EA")
LIGHT_BG = colors.HexColor("#F5F5F7")
SUMMARY_BG = colors.HexColor("#EEF6FF")
WHITE = colors.white


def _discover_font() -> tuple[str, str]:
    candidates = [
        settings.pdf_font_path,
        os.path.join(os.getenv("WINDIR", ""), "Fonts", "Deng.ttf") if os.getenv("WINDIR") else "",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for candidate in candidates:
        path = Path(candidate) if candidate else None
        if not path or not path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("RepairCN", str(path)))
            pdfmetrics.registerFontFamily(
                "RepairCN",
                normal="RepairCN",
                bold="RepairCN",
                italic="RepairCN",
                boldItalic="RepairCN",
            )
            return "RepairCN", str(path)
        except Exception:
            continue
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "ReportLab CID fallback"


FONT_NAME, FONT_SOURCE = _discover_font()


def _p(value: object) -> str:
    """Escape all user-controlled text before passing it to ReportLab Paragraph."""
    return html.escape("" if value is None else str(value), quote=True).replace("\n", "<br/>")


def _money(value: object) -> str:
    try:
        return f"{Decimal(str(value or 0)):,.2f}"
    except Exception:
        return "0.00"


def _number(value: object) -> str:
    try:
        number = Decimal(str(value or 0))
        return f"{number:f}".rstrip("0").rstrip(".") or "0"
    except Exception:
        return "0"


def _date_text(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M") if value.tzinfo else value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value or "-")


def _device_name(brand: str | None, model: str | None) -> str:
    brand_text = (brand or "").strip()
    model_text = (model or "").strip()
    if not brand_text:
        return model_text or "-"
    if model_text.lower().startswith(brand_text.lower()):
        return model_text
    return f"{brand_text} {model_text}".strip()


def _safe_url(value: object) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _legacy_public_config() -> dict[str, str]:
    path = BASE_DIR / "config.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    allowed = {"pdf_title", "pdf_footer", "logo_text", "logo_path", "pay_url"}
    return {key: str(data.get(key) or "") for key in allowed}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "ServiceBody",
        parent=base["BodyText"],
        fontName=FONT_NAME,
        fontSize=9.4,
        leading=15.2,
        textColor=TEXT_BLUE,
        wordWrap="CJK",
        spaceAfter=0,
    )
    return {
        "body": body,
        "small": ParagraphStyle("ServiceSmall", parent=body, fontSize=8, leading=12.5, textColor=MUTED_BLUE),
        "label": ParagraphStyle("ServiceLabel", parent=body, fontSize=8.2, leading=13, textColor=MUTED_BLUE),
        "header": ParagraphStyle("ServiceHeader", parent=body, fontSize=9, leading=14, textColor=JAZZ_BLUE),
        "title": ParagraphStyle("ServiceTitle", parent=body, fontSize=22, leading=29, textColor=JAZZ_BLUE),
        "subtitle": ParagraphStyle("ServiceSubtitle", parent=body, fontSize=11, leading=17, textColor=JAZZ_BLUE),
        "section": ParagraphStyle("ServiceSection", parent=body, fontSize=10.5, leading=16, textColor=JAZZ_BLUE, spaceBefore=3, spaceAfter=4),
        "right": ParagraphStyle("ServiceRight", parent=body, alignment=TA_RIGHT),
        "center": ParagraphStyle("ServiceCenter", parent=body, alignment=TA_CENTER),
        "amount": ParagraphStyle("ServiceAmount", parent=body, alignment=TA_RIGHT),
        "total": ParagraphStyle("ServiceTotal", parent=body, alignment=TA_RIGHT, fontSize=11.5, leading=17, textColor=ACCENT_BLUE),
        "header_right": ParagraphStyle("ServiceHeaderRight", parent=body, alignment=TA_RIGHT, fontSize=8.5, leading=12, textColor=MUTED_BLUE),
        "header_left": ParagraphStyle("ServiceHeaderLeft", parent=body, alignment=TA_LEFT, fontSize=12.5, leading=16, textColor=JAZZ_BLUE),
        "body_box": ParagraphStyle(
            "ServiceBodyBox",
            parent=body,
            backColor=LIGHT_BG,
            borderColor=LIGHT_BORDER,
            borderWidth=0.4,
            borderPadding=10,
            spaceBefore=0,
            spaceAfter=0,
        ),
    }


def _qr_drawing(value: str, size: float = 27 * mm) -> Drawing:
    widget = QrCodeWidget(value)
    x1, y1, x2, y2 = widget.getBounds()
    width, height = x2 - x1, y2 - y1
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(widget)
    return drawing


class PdfReportService:
    def __init__(self, report_dir: Path | None = None, *, brand_name: str | None = None):
        self.report_dir = (report_dir or settings.report_dir).resolve()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        legacy = _legacy_public_config()
        self.brand_name = (brand_name or settings.pdf_brand_name or "服务品牌").strip() or "服务品牌"
        self.quote_title = settings.pdf_quote_title or f"{self.brand_name}服务报价单"
        self.footer_text = settings.pdf_footer_text or f"感谢您选择 {self.brand_name}"
        logo_value = settings.pdf_logo_path or legacy.get("logo_path") or ""
        logo_path = Path(logo_value).expanduser() if logo_value else None
        if logo_path and not logo_path.is_absolute():
            logo_path = (BASE_DIR / logo_path).resolve()
        self.logo_path = logo_path if logo_path and logo_path.is_file() else None

    def _page_callback(self, report_kind: str, document_no: str):
        styles = _styles()

        def draw(canvas, doc) -> None:
            canvas.saveState()
            page_width, page_height = A4
            left, right = 18 * mm, page_width - 18 * mm
            header_top = page_height - 11 * mm
            if self.logo_path:
                try:
                    canvas.drawImage(
                        str(self.logo_path), left, header_top - 13 * mm,
                        width=31 * mm, height=12 * mm,
                        preserveAspectRatio=True, anchor="sw", mask="auto",
                    )
                except Exception:
                    brand = Paragraph(_p(self.brand_name), styles["header_left"])
                    brand.wrapOn(canvas, 62 * mm, 14 * mm)
                    brand.drawOn(canvas, left, header_top - 10 * mm)
            else:
                brand = Paragraph(_p(self.brand_name), styles["header_left"])
                brand.wrapOn(canvas, 62 * mm, 14 * mm)
                brand.drawOn(canvas, left, header_top - 10 * mm)

            meta = Paragraph(
                f"{_p(report_kind)}<br/><font size='8'>{_p(document_no)}</font>",
                styles["header_right"],
            )
            meta_width = 82 * mm
            _, meta_height = meta.wrap(meta_width, 17 * mm)
            meta.drawOn(canvas, right - meta_width, header_top - meta_height)

            canvas.setFillColor(ACCENT_BLUE)
            canvas.circle(left + 1.2 * mm, page_height - 27.1 * mm, 1.2 * mm, fill=1, stroke=0)
            canvas.setStrokeColor(LIGHT_BORDER)
            canvas.setLineWidth(0.55)
            canvas.line(left + 5 * mm, page_height - 27.1 * mm, right, page_height - 27.1 * mm)

            canvas.setStrokeColor(LIGHT_BORDER)
            canvas.setLineWidth(0.4)
            canvas.line(left, 16 * mm, right, 16 * mm)
            canvas.setFont(FONT_NAME, 8)
            canvas.setFillColor(MUTED_BLUE)
            canvas.drawString(left, 11 * mm, self.brand_name[:60])
            canvas.drawCentredString(page_width / 2, 11 * mm, self.footer_text[:100])
            canvas.setFont("Helvetica", 8)
            canvas.drawRightString(right, 11 * mm, f"Page {canvas.getPageNumber()}")
            canvas.restoreState()

        return draw

    def _atomic_build(self, destination: Path, story: list, title: str, *, report_kind: str, document_no: str) -> Path:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination.stem}-", suffix=".pdf.tmp", dir=destination.parent
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            doc = SimpleDocTemplate(
                str(temp_path),
                pagesize=A4,
                rightMargin=18 * mm,
                leftMargin=18 * mm,
                topMargin=34 * mm,
                bottomMargin=22 * mm,
                title=title,
                author=self.brand_name,
                subject=report_kind,
                pageCompression=1,
            )
            callback = self._page_callback(report_kind, document_no)
            doc.build(story, onFirstPage=callback, onLaterPages=callback)
            if not temp_path.is_file() or temp_path.stat().st_size < 100:
                raise RuntimeError("PDF generation produced an invalid temporary file")
            os.replace(temp_path, destination)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return destination

    @staticmethod
    def _title_block(title: str, eyebrow: str, meta_lines: list[str], styles: dict[str, ParagraphStyle]) -> list:
        meta_html = "<br/>".join(_p(line) for line in meta_lines)
        title_table = Table(
            [[Paragraph(f"<font size='8' color='#007AFF'>{_p(eyebrow)}</font><br/>{_p(title)}", styles["title"]), Paragraph(meta_html, styles["right"])]],
            colWidths=[108 * mm, 66 * mm],
        )
        title_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 8),
            ("TOPPADDING", (0, 0), (0, 0), 0),
            ("BOTTOMPADDING", (0, 0), (0, 0), 0),
            ("BACKGROUND", (1, 0), (1, 0), LIGHT_BG),
            ("BOX", (1, 0), (1, 0), 0.4, LIGHT_BORDER),
            ("LEFTPADDING", (1, 0), (1, 0), 9),
            ("RIGHTPADDING", (1, 0), (1, 0), 9),
            ("TOPPADDING", (1, 0), (1, 0), 7),
            ("BOTTOMPADDING", (1, 0), (1, 0), 7),
        ]))
        return [title_table, Spacer(1, 8 * mm)]

    @staticmethod
    def _info_card(rows: list[tuple[object, object, object, object]], styles: dict[str, ParagraphStyle]) -> Table:
        rendered = []
        for left_label, left_value, right_label, right_value in rows:
            rendered.append([
                Paragraph(_p(left_label), styles["label"]),
                Paragraph(_p(left_value), styles["body"]),
                Paragraph(_p(right_label), styles["label"]),
                Paragraph(_p(right_value), styles["body"]),
            ])
        table = Table(rendered, colWidths=[24 * mm, 52 * mm, 24 * mm, 74 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.4, LIGHT_BORDER),
            ("LINEBELOW", (0, 0), (-1, -2), 0.3, LIGHT_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        return table

    @staticmethod
    def _text_section(
        title: str,
        value: object,
        styles: dict[str, ParagraphStyle],
        *,
        accent: bool = False,
        min_space_mm: float = 35,
    ) -> list:
        bar_color = ACCENT_BLUE
        heading = Table(
            [["", Paragraph(_p(title), styles["section"])]],
            colWidths=[2 * mm, 172 * mm],
        )
        heading.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), bar_color),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        heading._keepWithNext = True
        text = Paragraph(_p(value or "-"), styles["body_box"])
        return [
            CondPageBreak(min_space_mm * mm),
            heading,
            Spacer(1, 1.5 * mm),
            text,
            Spacer(1, 5 * mm),
        ]

    @staticmethod
    def _assessment_section(quote: Quote, styles: dict[str, ParagraphStyle]) -> list:
        """Render the frozen repair assessment as one readable customer-facing block."""
        heading = Table(
            [["", Paragraph("定损与维修说明", styles["section"])]],
            colWidths=[2 * mm, 172 * mm],
        )
        heading.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), ACCENT_BLUE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        rows = [
            ("定损结果", getattr(quote, "assessment_result", None)),
            ("责任判定", getattr(quote, "assessment_responsibility", None)),
            ("维修建议", getattr(quote, "repair_recommendation", None)),
            ("客户须知", getattr(quote, "customer_notice", None)),
        ]
        body = Table(
            [[Paragraph(_p(label), styles["label"]), Paragraph(_p(value or "-"), styles["body_box"])] for label, value in rows],
            colWidths=[28 * mm, 146 * mm],
        )
        body.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.4, LIGHT_BORDER),
            ("LINEBELOW", (0, 0), (-1, -2), 0.3, LIGHT_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        return [CondPageBreak(48 * mm), heading, Spacer(1, 1.5 * mm), body, Spacer(1, 6 * mm)]

    def quote(self, quote: Quote) -> Path:
        """Build the customer-facing quote from sell prices and public totals only."""
        order = getattr(quote, "repair_order", None)
        ticket = getattr(quote, "service_ticket", None)
        customer = order.customer if order else ticket.customer
        device = order.device if order else ticket.device
        styles = _styles()
        is_replacement = bool(ticket and ticket.ticket_type == "replacement")
        if is_replacement:
            title = "置换服务报价单"
            eyebrow = "REPLACEMENT QUOTATION"
        elif ticket:
            title = "零售报价单"
            eyebrow = "RETAIL QUOTATION"
        else:
            title = self.quote_title
            eyebrow = "SERVICE QUOTATION"
        business_no = order.order_no if order else ticket.ticket_no
        device_name = _device_name(device.brand, device.model) if device else (
            "旧机 / 置换方案" if is_replacement else "零售商品 / 服务"
        )
        serial_number = device.serial_number if device else "-"
        # A service ticket's public subject is its title.  The description belongs
        # in the detailed problem section, not in the service-theme metadata row.
        service_theme = (ticket.title if ticket else getattr(order, "title", None)) or (
            order.fault_description if order else "-"
        )
        customer_notes = order.customer_notes if order else None
        story = self._title_block(
            title,
            eyebrow,
            [f"报价编号  {quote.quote_no}", f"版本  V{quote.version}", f"签发时间  {_date_text(quote.created_at)}"],
            styles,
        )
        story.extend([
            self._info_card([
                ("客户", customer.name, "联系电话", customer.phone or "-"),
                ("工单号", business_no, "设备 / 场景", device_name),
                ("序列号", serial_number, "报价状态", quote.status),
                ("服务主题", service_theme, "客户邮箱", customer.email or "-"),
            ], styles),
            Spacer(1, 7 * mm),
        ])

        if order:
            story.extend(self._assessment_section(quote, styles))
        elif is_replacement:
            replacement_lines = [
                f"旧机检测结果：{ticket.replacement_inspection_result or '待填写'}",
                f"旧机抵折：{'¥ ' + _money(ticket.trade_in_credit) if ticket.trade_in_credit is not None else '待填写'}",
                f"寄回门店单号 / 线下交易备注：{ticket.return_reference or '待登记'}",
                f"寄出客户单号：{ticket.outbound_to_customer_tracking_no or '待登记'}",
            ]
            story.extend(self._text_section(
                "置换业务信息",
                "\n".join(replacement_lines),
                styles,
                accent=True,
            ))

        rows: list[list[Paragraph]] = [[
            Paragraph("服务项目 / 物料", styles["header"]),
            Paragraph("类型", styles["header"]),
            Paragraph("数量", styles["header"]),
            Paragraph("单价", styles["header"]),
            Paragraph("金额", styles["header"]),
        ]]
        type_labels = {"part": "配件", "labor": "人工", "service": "服务", "discount": "优惠", "shipping": "运费"}
        for item in quote.items:
            rows.append([
                Paragraph(_p(item.item_name), styles["body"]),
                Paragraph(_p(type_labels.get(item.item_type, item.item_type or "项目")), styles["body"]),
                Paragraph(_p(_number(item.quantity)), styles["amount"]),
                Paragraph(f"¥ {_money(item.unit_price)}", styles["amount"]),
                Paragraph(f"¥ {_money(item.amount)}", styles["amount"]),
            ])

        summary_start = len(rows)
        summaries = [
            ("配件 / 项目小计", quote.subtotal),
            ("人工服务费", quote.labor_fee),
            ("运费", quote.shipping_fee),
            ("旧机抵折 / 优惠" if is_replacement else "优惠", -Decimal(str(quote.discount or 0))),
            ("总计费用  Grand Total", quote.total_amount),
        ]
        for label, amount in summaries:
            rows.append([
                Paragraph(_p(label), styles["body"]),
                Paragraph("", styles["body"]),
                Paragraph("", styles["body"]),
                Paragraph("", styles["body"]),
                Paragraph(f"¥ {_money(amount)}", styles["total"] if label.startswith("总计") else styles["amount"]),
            ])

        table = LongTable(
            rows,
            colWidths=[74 * mm, 24 * mm, 19 * mm, 27 * mm, 30 * mm],
            repeatRows=1,
            splitByRow=1,
        )
        table_commands = [
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), JAZZ_BLUE),
            ("LINEABOVE", (0, 0), (-1, 0), 1.2, ACCENT_BLUE),
            ("LINEBELOW", (0, 1), (-1, -1), 0.35, LIGHT_BORDER),
            ("BOX", (0, 0), (-1, -1), 0.4, LIGHT_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("BACKGROUND", (0, summary_start), (-1, -2), LIGHT_BG),
            ("BACKGROUND", (0, -1), (-1, -1), SUMMARY_BG),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT_BLUE),
            ("LINEBELOW", (0, -1), (-1, -1), 1.2, ACCENT_BLUE),
        ]
        for row_index in range(summary_start, len(rows)):
            table_commands.append(("SPAN", (0, row_index), (3, row_index)))
        table.setStyle(TableStyle(table_commands))
        story.extend([table, Spacer(1, 6 * mm)])

        if customer_notes:
            story.extend(self._text_section("客户可见服务备注", customer_notes, styles, accent=True))

        qr_value = _safe_url(getattr(quote, "payment_url", None))
        change_scope = "置换方案、旧机抵折或其他费用" if is_replacement else (
            "服务范围、物料或其他费用" if ticket else "维修、领料或其他费用"
        )
        confirm_text = Paragraph(
            "<font color='#1D1D1F'>报价确认说明</font><br/>"
            f"本报价以当前版本的冻结项目和金额为准；报价不等同于已收款。{change_scope}发生变化时，应生成新版本并由客户重新确认。",
            styles["body"],
        )
        if qr_value:
            qr_box = Table(
                [[_qr_drawing(qr_value)], [Paragraph("扫码打开付款页面", styles["small"])]],
                colWidths=[36 * mm],
            )
            qr_box.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOX", (0, 0), (-1, -1), 0.4, LIGHT_BORDER),
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            confirm = Table([[confirm_text, qr_box]], colWidths=[134 * mm, 40 * mm])
        else:
            confirm = Table([[confirm_text]], colWidths=[174 * mm])
        confirm_style = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 10 if qr_value else 0),
        ]
        if qr_value:
            confirm_style.extend([
                ("LEFTPADDING", (1, 0), (1, 0), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ])
        confirm.setStyle(TableStyle(confirm_style))
        story.extend([CondPageBreak(48 * mm), confirm])
        return self._atomic_build(
            self.report_dir / f"{quote.quote_no}.pdf",
            story,
            f"{title} {quote.quote_no}",
            report_kind="报价单",
            document_no=quote.quote_no,
        )

    def repair_report(self, order: RepairOrder, *, completed: bool = False) -> Path:
        """Build a customer-safe repair work order in the compact CAS-style layout."""
        styles = _styles()
        title = "维修完成单" if completed else "维修工单"
        english = "REPAIR COMPLETION" if completed else "REPAIR WORK ORDER"
        customer, device = order.customer, order.device
        quotes = [
            quote for quote in getattr(order, "quotes", [])
            if getattr(quote, "deleted_at", None) is None
        ]
        quote = max(quotes, key=lambda item: item.version, default=None)

        story = self._title_block(
            title,
            english,
            [
                f"工单号  {order.order_no}",
                f"生成时间  {_date_text(datetime.now().astimezone())}",
                f"当前状态  {order.status}",
            ],
            styles,
        )
        story.extend([
            self._info_card([
                ("客户", customer.name, "工单号", order.order_no),
                ("设备", _device_name(device.brand, device.model), "序列号", device.serial_number),
                ("收机时间", _date_text(order.received_at), "报告日期", _date_text(datetime.now().astimezone())),
                ("联系电话", customer.phone or "-", "报价状态", quote.status if quote else "未报价"),
            ], styles),
            Spacer(1, 5 * mm),
        ])

        remark_lines = [f"客户报修：{order.fault_description}"]
        if order.intake_condition:
            remark_lines.append(f"收机状态：{order.intake_condition}")
        if order.intake_accessories:
            remark_lines.append(f"随附物品：{order.intake_accessories}")
        if order.internal_notes:
            remark_lines.append(f"检测 / 维修结果：{order.internal_notes}")
        if quote:
            for label, value in [
                ("定损结果", getattr(quote, "assessment_result", None)),
                ("责任判定", getattr(quote, "assessment_responsibility", None)),
                ("维修建议", getattr(quote, "repair_recommendation", None)),
                ("客户须知", getattr(quote, "customer_notice", None)),
            ]:
                if value:
                    remark_lines.append(f"{label}：{value}")
        story.extend(self._text_section("备注", "\n".join(remark_lines), styles, accent=True))

        item_rows: list[list[Paragraph]] = [[
            Paragraph("消耗物料 / 服务项目", styles["header"]),
            Paragraph("数量", styles["header"]),
            Paragraph("单价", styles["header"]),
            Paragraph("总额", styles["header"]),
        ]]
        if quote and quote.items:
            for item in quote.items:
                item_rows.append([
                    Paragraph(_p(item.item_name), styles["body"]),
                    Paragraph(_p(_number(item.quantity)), styles["amount"]),
                    Paragraph(f"¥ {_money(item.unit_price)}", styles["amount"]),
                    Paragraph(f"¥ {_money(item.amount)}", styles["amount"]),
                ])
        else:
            item_rows.append([
                Paragraph("暂无已冻结的报价项目", styles["body"]),
                Paragraph("-", styles["amount"]),
                Paragraph("-", styles["amount"]),
                Paragraph("-", styles["amount"]),
            ])

        summary_start = len(item_rows)
        if quote:
            summaries = [
                ("项目小计", quote.subtotal),
                ("人工服务费", quote.labor_fee),
                ("运费", quote.shipping_fee),
                ("优惠金额", -Decimal(str(quote.discount or 0))),
                ("报价金额", quote.total_amount),
            ]
        else:
            summaries = [("报价金额", order.total_quote_amount)]
        received_amount = Decimal(str(order.total_received or 0))
        quote_amount = Decimal(str(quote.total_amount if quote else order.total_quote_amount or 0))
        summaries.extend([
            ("已收款", received_amount),
            ("待付金额", max(Decimal("0"), quote_amount - received_amount)),
        ])
        for label, amount in summaries:
            item_rows.append([
                Paragraph(_p(label), styles["total"] if label == "报价金额" else styles["body"]),
                Paragraph("", styles["body"]),
                Paragraph("", styles["body"]),
                Paragraph(f"¥ {_money(amount)}", styles["total"] if label == "报价金额" else styles["amount"]),
            ])
        items_table = LongTable(item_rows, colWidths=[92 * mm, 20 * mm, 30 * mm, 32 * mm], repeatRows=1, splitByRow=1)
        table_commands = [
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), JAZZ_BLUE),
            ("LINEABOVE", (0, 0), (-1, 0), 1.2, ACCENT_BLUE),
            ("LINEBELOW", (0, 1), (-1, -1), 0.35, LIGHT_BORDER),
            ("BOX", (0, 0), (-1, -1), 0.4, LIGHT_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("BACKGROUND", (0, summary_start), (-1, -2), LIGHT_BG),
            ("BACKGROUND", (0, -1), (-1, -1), SUMMARY_BG),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT_BLUE),
            ("LINEBELOW", (0, -1), (-1, -1), 1.2, ACCENT_BLUE),
        ]
        for row_index in range(summary_start, len(item_rows)):
            table_commands.append(("SPAN", (0, row_index), (2, row_index)))
        items_table.setStyle(TableStyle(table_commands))
        story.extend([CondPageBreak(55 * mm), Paragraph("维修项目与费用", styles["section"]), items_table, Spacer(1, 5 * mm)])

        notice_lines = [
            "维修范围以当前工单及客户确认的报价版本为准，新增项目需再次确认。",
            "维修前请客户自行备份重要数据；因数据、第三方软件或不可修复损坏造成的风险需另行确认。",
        ]
        if order.customer_notes:
            notice_lines.append(order.customer_notes)
        story.append(KeepTogether(self._text_section(
            "注意事项",
            "\n".join(f"{index}、{value}" for index, value in enumerate(notice_lines, 1)),
            styles,
            min_space_mm=0,
        )))
        story.append(Paragraph("本单用于记录设备收机、检测、维修与费用确认信息。", styles["small"]))
        suffix = "completion" if completed else "inspection"
        return self._atomic_build(
            self.report_dir / f"{order.order_no}-{suffix}.pdf",
            story,
            f"{title} {order.order_no}",
            report_kind=title,
            document_no=order.order_no,
        )

    def service_ticket(
        self,
        ticket: ServiceTicket,
        *,
        customer_notes: list[ServiceTicketNote] | None = None,
    ) -> Path:
        """Build a customer-safe service ticket report without internal notes."""
        styles = _styles()
        customer, device = ticket.customer, ticket.device
        owner, group = ticket.current_owner, ticket.processing_group
        type_labels = {
            "repair": "维修工单",
            "consultation": "客户咨询",
            "quote_followup": "报价跟进",
            "after_sales_followup": "售后回访",
            "complaint": "投诉处理",
            "logistics_exception": "物流异常",
            "technical_support": "技术支持",
            "specialist_assistance": "高级专员协助",
            "retail": "零售",
            "replacement": "置换业务",
        }
        status_labels = {
            "open": "待分派",
            "assigned": "已分派",
            "in_progress": "处理中",
            "waiting_customer": "等待客户",
            "waiting_internal": "等待内部",
            "resolved": "已解决",
            "closed": "已关闭",
            "cancelled": "已取消",
        }
        priority_labels = {"low": "低", "normal": "普通", "high": "加急", "urgent": "紧急"}
        title = "服务工单报告"
        story = self._title_block(
            title,
            "DIGITAL SERVICE SUPPORT REPORT",
            [
                f"服务单号  {ticket.ticket_no}",
                f"生成时间  {_date_text(datetime.now().astimezone())}",
                f"当前状态  {status_labels.get(ticket.status, ticket.status)}",
            ],
            styles,
        )
        story.extend([
            self._info_card([
                ("客户", customer.name if customer else "-", "联系电话", customer.phone if customer and customer.phone else "-"),
                ("工单类型", type_labels.get(ticket.ticket_type, ticket.ticket_type), "优先级", priority_labels.get(ticket.priority, ticket.priority)),
                ("设备", _device_name(device.brand, device.model) if device else "-", "序列号", device.serial_number if device else "-"),
                ("当前负责人", owner.display_name if owner else "待分派", "处理组", group.name if group else "待分组"),
                ("创建时间", _date_text(ticket.created_at), "处理时限", _date_text(ticket.due_at)),
            ], styles),
            Spacer(1, 7 * mm),
        ])
        story.extend(self._text_section("服务主题", ticket.title, styles))
        story.extend(self._text_section("问题描述 / 本次反馈", ticket.description, styles, accent=True))
        if ticket.ticket_type == "replacement":
            replacement_lines = [
                f"检测结果：{ticket.replacement_inspection_result or '待填写'}",
                f"置换抵扣：{'¥ ' + _money(ticket.trade_in_credit) if ticket.trade_in_credit is not None else '待填写'}",
                f"退回门店：{ticket.return_reference or '待登记'}",
                f"寄送客户：{ticket.outbound_to_customer_tracking_no or '待登记'}",
            ]
            story.extend(self._text_section("置换业务信息", "\n".join(replacement_lines), styles, accent=True))

        visible_notes = customer_notes or []
        if visible_notes:
            note_text = "\n\n".join(
                f"{_date_text(note.created_at)}  {note.content}" for note in visible_notes
            )
        else:
            note_text = "暂无客户可见的补充记录。"
        story.extend(self._text_section("客户可见处理记录", note_text, styles))

        result_lines = [f"当前状态：{status_labels.get(ticket.status, ticket.status)}"]
        if ticket.first_response_at:
            result_lines.append(f"首次响应：{_date_text(ticket.first_response_at)}")
        if ticket.resolved_at:
            result_lines.append(f"解决时间：{_date_text(ticket.resolved_at)}")
        if ticket.closed_at:
            result_lines.append(f"关闭时间：{_date_text(ticket.closed_at)}")
        story.extend(self._text_section("处理状态", "\n".join(result_lines), styles))
        story.extend([
            CondPageBreak(28 * mm),
            Paragraph(
                "本报告仅包含客户可见的服务信息；内部备注、内部协作记录和权限信息不会写入可外发 PDF。",
                styles["small"],
            ),
        ])
        return self._atomic_build(
            self.report_dir / f"{ticket.ticket_no}-service.pdf",
            story,
            f"{title} {ticket.ticket_no}",
            report_kind=title,
            document_no=ticket.ticket_no,
        )
