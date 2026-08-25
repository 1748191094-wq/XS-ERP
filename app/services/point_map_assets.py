from __future__ import annotations

from io import BytesIO

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from app.core.exceptions import BusinessError


def _longest_dense_segment(active: np.ndarray, weights: np.ndarray) -> tuple[int, int] | None:
    starts = np.flatnonzero(active & np.r_[True, ~active[:-1]])
    ends = np.flatnonzero(active & np.r_[~active[1:], True]) + 1
    if not len(starts):
        return None
    candidates = list(zip(starts.tolist(), ends.tolist()))
    return max(candidates, key=lambda bounds: float(weights[bounds[0]:bounds[1]].sum()) * (bounds[1] - bounds[0]))


def crop_dense_board(image: Image.Image) -> Image.Image:
    """Remove artwork-film whitespace while retaining the densest board drawing."""
    gray = np.asarray(image.convert("L"))
    ink = gray < 245
    height, width = ink.shape
    row_counts = ink.sum(axis=1)
    row_segment = _longest_dense_segment(row_counts > max(12, width * 0.012), row_counts)
    if not row_segment:
        return image
    y0, y1 = row_segment
    band = ink[y0:y1]
    col_counts = band.sum(axis=0)
    col_segment = _longest_dense_segment(col_counts > max(8, (y1 - y0) * 0.018), col_counts)
    if not col_segment:
        return image
    x0, x1 = col_segment
    pad_x = max(12, int((x1 - x0) * 0.04))
    pad_y = max(12, int((y1 - y0) * 0.04))
    box = (max(0, x0 - pad_x), max(0, y0 - pad_y), min(width, x1 + pad_x), min(height, y1 + pad_y))
    if (box[2] - box[0]) * (box[3] - box[1]) < width * height * 0.02:
        return image
    return image.crop(box)


def render_pdf_page_png(content: bytes, page_number: int = 1, *, auto_crop: bool = True) -> bytes:
    try:
        document = pdfium.PdfDocument(content)
    except Exception as exc:
        raise BusinessError("无法读取位号图 PDF", code="invalid_point_map_pdf", status_code=422) from exc
    if page_number < 1 or page_number > len(document):
        raise BusinessError(
            f"PDF 共 {len(document)} 页，页码必须在 1 到 {len(document)} 之间",
            code="point_map_page_out_of_range", status_code=422,
        )
    try:
        page = document[page_number - 1]
        image = page.render(scale=3.0).to_pil().convert("RGB")
        if auto_crop:
            image = crop_dense_board(image)
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()
    except BusinessError:
        raise
    except Exception as exc:
        raise BusinessError("位号图 PDF 页面转换失败", code="point_map_render_failed", status_code=422) from exc
    finally:
        document.close()
