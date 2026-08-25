from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.core.security import safe_filename


@dataclass(slots=True)
class StoredFile:
    original_filename: str
    storage_path: str
    file_size: int
    sha256: str
    content_type: str


_SAFE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".zip": "application/zip",
    ".ulg": "application/octet-stream",
    ".dat": "application/octet-stream",
    ".bin": "application/octet-stream",
}


def safe_attachment_content_type(filename: str) -> str:
    return _SAFE_CONTENT_TYPES.get(
        Path(filename or "").suffix.lower(), "application/octet-stream"
    )


def _validate_content_signature(
    filename: str, content: bytes, *, allow_binary_text: bool = False
) -> str:
    suffix = Path(filename).suffix.lower()
    valid = True
    if suffix in {".jpg", ".jpeg"}:
        valid = content.startswith(b"\xff\xd8\xff")
    elif suffix == ".png":
        valid = content.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix == ".webp":
        valid = len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    elif suffix in {".mp4", ".mov"}:
        valid = len(content) >= 12 and content[4:8] == b"ftyp"
    elif suffix == ".pdf":
        valid = content[:1024].lstrip().startswith(b"%PDF-")
    elif suffix == ".zip":
        valid = content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
    elif suffix == ".ulg":
        valid = content.startswith(b"ULog")
    elif suffix in {".txt", ".csv"}:
        valid = b"\x00" not in content
        if valid:
            try:
                content.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    content.decode("gb18030")
                except UnicodeDecodeError:
                    valid = False
        if not valid and allow_binary_text and suffix == ".txt":
            return "application/octet-stream"
    if not valid:
        raise BusinessError(
            "文件内容与扩展名不一致",
            code="file_signature_mismatch",
            status_code=415,
        )
    return safe_attachment_content_type(filename)


class LocalStorageService:
    def __init__(self, root: Path | None = None):
        self.root = (root or settings.upload_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_bytes(
        self,
        filename: str,
        content: bytes,
        *,
        folder: str = "attachments",
        allow_binary_text: bool = False,
    ) -> StoredFile:
        if len(content) > settings.max_upload_bytes:
            raise BusinessError("文件超过上传大小限制", code="file_too_large", status_code=413)
        suffix = Path(filename).suffix.lower()
        if suffix not in settings.allowed_attachment_extensions:
            raise BusinessError(f"不允许上传 {suffix or '无扩展名'} 文件", code="file_type_not_allowed", status_code=415)
        content_type = _validate_content_signature(
            filename, content, allow_binary_text=allow_binary_text
        )
        target_dir = (self.root / folder / datetime.now().strftime("%Y/%m")).resolve()
        if self.root not in target_dir.parents and target_dir != self.root:
            raise BusinessError("非法存储路径", code="unsafe_storage_path")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_filename(filename)
        fd, temp_name = tempfile.mkstemp(prefix=target.stem + "-", suffix=".tmp", dir=target_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise
        return StoredFile(
            filename,
            str(target.relative_to(self.root)),
            len(content),
            hashlib.sha256(content).hexdigest(),
            content_type,
        )

    def absolute_path(self, storage_path: str) -> Path:
        target = (self.root / storage_path).resolve()
        if self.root not in target.parents:
            raise BusinessError("非法文件路径", code="unsafe_storage_path")
        return target
