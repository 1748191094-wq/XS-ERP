from io import BytesIO

from PIL import Image
from reportlab.pdfgen import canvas

from app.services.point_map_assets import render_pdf_page_png


def test_pdf_point_map_page_is_rendered_and_dense_content_is_cropped():
    source = BytesIO()
    pdf = canvas.Canvas(source, pagesize=(1000, 700))
    pdf.setFillColorRGB(0.88, 0.88, 0.88)
    pdf.rect(540, 80, 360, 250, fill=1)
    pdf.setFillColorRGB(0, 0, 0)
    for index in range(18):
        x = 565 + (index % 6) * 48
        y = 105 + (index // 6) * 70
        pdf.rect(x, y, 24, 18)
        pdf.drawString(x, y + 23, f"U{index + 1}")
    pdf.showPage()
    pdf.save()

    rendered = render_pdf_page_png(source.getvalue(), 1, auto_crop=True)
    image = Image.open(BytesIO(rendered))
    assert image.format == "PNG"
    assert image.width > image.height
    assert image.width < 2000
    assert len(rendered) > 1000
