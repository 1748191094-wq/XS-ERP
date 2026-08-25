from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.storage.local import LocalStorageService, StoredFile


IMAGE_TYPES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}
VIDEO_TYPES = {
    "video/mp4": {".mp4"},
    "video/quicktime": {".mov"},
}


def save_client_image(
    *, filename: str, content_type: str | None, content: bytes, folder: str
) -> StoredFile:
    declared = (content_type or "").lower().split(";", 1)[0]
    suffix = Path(filename or "").suffix.lower()
    if declared not in IMAGE_TYPES or suffix not in IMAGE_TYPES[declared]:
        raise BusinessError(
            "仅支持 JPG、PNG 或 WebP 图片，且文件类型必须匹配",
            code="client_image_type_not_allowed",
            status_code=415,
        )
    if len(content) > settings.client_max_image_bytes:
        raise BusinessError("图片超过上传大小限制", code="file_too_large", status_code=413)
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
            detected = (image.format or "").upper()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise BusinessError(
            "图片内容无效或已损坏", code="invalid_image_content", status_code=415
        ) from exc
    expected = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[declared]
    if detected != expected:
        raise BusinessError(
            "图片真实格式与声明类型不一致",
            code="client_image_mime_mismatch",
            status_code=415,
        )
    return LocalStorageService().save_bytes(
        f"{uuid4().hex}{suffix}", content, folder=f"client/{folder}"
    )


def save_client_video(
    *, filename: str, content_type: str | None, content: bytes, folder: str
) -> StoredFile:
    declared = (content_type or "").lower().split(";", 1)[0]
    suffix = Path(filename or "").suffix.lower()
    if declared not in VIDEO_TYPES or suffix not in VIDEO_TYPES[declared]:
        raise BusinessError(
            "仅支持 MP4 或 MOV 视频，且文件类型必须匹配",
            code="client_video_type_not_allowed",
            status_code=415,
        )
    if len(content) > settings.client_max_video_bytes:
        raise BusinessError("视频超过上传大小限制", code="file_too_large", status_code=413)
    if len(content) < 16 or b"ftyp" not in content[:32]:
        raise BusinessError(
            "视频内容无效或格式不受支持", code="invalid_video_content", status_code=415
        )
    return LocalStorageService().save_bytes(
        f"{uuid4().hex}{suffix}", content, folder=f"client/{folder}"
    )
