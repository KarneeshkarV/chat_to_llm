from __future__ import annotations

import base64
import re
from io import BytesIO
from typing import Tuple

from fastapi import HTTPException

_DATA_URL_PATTERN = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)

_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/json": ".json",
}

_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/svg+xml",
}


def get_file_content_from_data_url(url: str) -> Tuple[bytes, str]:
    if not url or not url.startswith("data:"):
        raise HTTPException(
            status_code=400,
            detail="Only base64 data URLs are supported for image uploads.",
        )
    match = _DATA_URL_PATTERN.match(url)
    if not match:
        raise HTTPException(status_code=400, detail="Malformed data URL.")
    mime = match.group(1).strip().lower() or "application/octet-stream"
    b64 = match.group(2).strip()
    try:
        raw = base64.b64decode(b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to decode base64: %s" % exc)
    return raw, mime


def get_image_size(raw: bytes) -> Tuple[int, int]:
    try:
        from PIL import Image
    except ImportError:
        return 0, 0
    try:
        with Image.open(BytesIO(raw)) as img:
            return int(img.width), int(img.height)
    except Exception:
        return 0, 0


def determine_file_use_case(mime: str) -> str:
    mime_lower = (mime or "").lower()
    if mime_lower in _IMAGE_MIMES:
        return "multimodal"
    if mime_lower == "application/pdf":
        return "my_files"
    return "ace_upload"


def get_file_extension(mime: str) -> str:
    mime_lower = (mime or "").lower()
    return _MIME_EXTENSIONS.get(mime_lower, ".bin")
