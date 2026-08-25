from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from app.core.exceptions import BusinessError


POINT_MAP_NAME_MARKERS = ("位号图", "位置图", "丝印图", "点位图")


@dataclass(frozen=True, slots=True)
class PointMapSource:
    path: Path
    sha256: str
    page_count: int


def discover_point_map_sources(root: Path, limit: int) -> list[PointMapSource]:
    root = root.resolve()
    if not root.is_dir():
        raise BusinessError(
            f"点位图资料目录不存在：{root}",
            code="point_map_reference_root_missing",
            status_code=409,
        )
    candidates = sorted(
        (
            path.resolve()
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() == ".pdf"
            and any(marker in path.stem for marker in POINT_MAP_NAME_MARKERS)
        ),
        key=lambda path: (len(path.parts), str(path).casefold()),
    )
    by_digest: dict[str, Path] = {}
    for path in candidates:
        if root != path and root not in path.parents:
            continue
        content = path.read_bytes()
        by_digest.setdefault(hashlib.sha256(content).hexdigest(), path)
        if len(by_digest) >= max(1, limit):
            break

    sources: list[PointMapSource] = []
    for digest, path in by_digest.items():
        try:
            document = pdfium.PdfDocument(path)
            page_count = len(document)
            document.close()
        except Exception as exc:
            raise BusinessError(
                f"无法读取点位图 PDF：{path.name}",
                code="invalid_point_map_pdf",
                status_code=422,
            ) from exc
        sources.append(PointMapSource(path=path, sha256=digest, page_count=page_count))
    return sources


def infer_point_map_metadata(path: Path, digest: str, page_number: int, page_count: int) -> dict[str, str | None]:
    stem = re.sub(r"\s+", " ", path.stem).strip()
    folded = stem.casefold()
    path_text = " ".join(path.parts).casefold()

    series_rules = (
        ("mini", "Mini"), ("avata", "Avata"), ("fpv", "FPV"),
        ("air", "Air"), ("御", "Mavic"), ("悟", "Inspire"),
        ("tello", "Tello"), ("遥控", "遥控器"),
    )
    series = next((value for key, value in series_rules if key in path_text), "其他")
    product_category = "遥控器" if any(key in path_text for key in ("遥控", "rc pro", "rm5")) else "无人机"

    model_patterns = (
        r"DJI\s+Mavic\s+3(?:\s*&\s*Cine)?", r"Mavic\s+Air\s+2", r"DJI\s+Air\s+2S",
        r"DJI\s+Avata", r"DJI\s+FPV", r"DJI\s+Digital\s+FPV\s+System",
        r"DJI\s+RC(?:\s+Pro|-N1)?", r"RC\s+Pro", r"Mini\s*3\s*Pro", r"Mini\s*[123](?:\s*SE)?",
        r"M3[ET]", r"御\s*[23]", r"御\s*Pro", r"悟\s*3", r"(?:WM|RM|RC)[-_ ]?\d{3}",
    )
    model = next((match.group(0) for pattern in model_patterns if (match := re.search(pattern, stem, re.I))), None)
    if not model:
        model = series if series != "其他" else re.split(r"[_-]", stem, maxsplit=1)[0][:160]

    module_rules = (
        "充电管家", "遥控器核心板", "遥控器主板", "飞行器核心板", "核心板", "电调板组件", "电调板",
        "电源板", "GPS板", "IMU板", "下视觉", "下视TOF", "下视", "上视", "后视板", "云台电调板",
        "云台电调", "相机主板", "射频板", "图传BB板", "按键板", "补光灯板", "补光灯", "中心板", "三合一排线",
    )
    module_name = next((value for value in module_rules if value.casefold() in folded), "板件")
    board_match = re.search(r"\b(?:PP|WM|RM|RC)[-_ ]?\d{3,}\b", stem, re.I)
    board_code = board_match.group(0).replace(" ", "_") if board_match else None
    title = stem if page_count == 1 else f"{stem} · 第 {page_number} 页"
    return {
        "brand": "DJI",
        "product_category": product_category,
        "series": series,
        "model_pattern": model[:160],
        "module_name": module_name,
        "board_code": board_code[:120] if board_code else None,
        "title": title[:240],
        "version": f"资料-{digest[:8]}",
    }
