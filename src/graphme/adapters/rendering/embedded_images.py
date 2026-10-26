"""Pictures inside an Office file held to what a page can show, before the viewer draws it.

The viewer refuses any picture past 2^27 decoded pixels and fails the whole file; a photograph
from a camera or a scan pasted into a slide is often past that. Nothing larger than a page is
ever seen: a page is read at most `MODEL_IMAGE_LONG_EDGE` on its long edge (`chat.py`), and is
drawn at twice the size it is read at, so a picture is held to twice that long edge. The file is
never touched; a copy with the large pictures scaled down is drawn instead, and a file whose
pictures all fit is drawn from itself.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PIL import Image

from graphme.adapters.chat import MODEL_IMAGE_LONG_EDGE

EMBEDDED_LONG_EDGE = 2 * MODEL_IMAGE_LONG_EDGE
_RASTER = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp")


def _too_large(data: bytes) -> bool:
    # Only the header is read; Pillow's guard against huge pictures would refuse to say how
    # large the very ones this is for are.
    limit, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return max(image.size) > EMBEDDED_LONG_EDGE
    except (OSError, ValueError):
        return False  # not a picture this side reads: the viewer's to judge
    finally:
        Image.MAX_IMAGE_PIXELS = limit


def _scaled(data: bytes, name: str) -> bytes:
    limit, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, None  # decoded once, here only
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.draft(image.mode, (EMBEDDED_LONG_EDGE, EMBEDDED_LONG_EDGE))  # JPEG: decode small
            image.thumbnail((EMBEDDED_LONG_EDGE, EMBEDDED_LONG_EDGE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            kind = "JPEG" if name.lower().endswith((".jpg", ".jpeg")) else "PNG"
            (image.convert("RGB") if kind == "JPEG" else image).save(
                out, format=kind, **({"quality": 90} if kind == "JPEG" else {}))
            return out.getvalue()
    except (OSError, ValueError):
        return data
    finally:
        Image.MAX_IMAGE_PIXELS = limit


def lightened(source: Path, workspace: Path) -> Path:
    """The file to draw: `source` itself, or a copy under `workspace` with every picture past
    `EMBEDDED_LONG_EDGE` scaled down to it. A PNG stays a PNG and a JPEG a JPEG; any other
    picture is written as PNG under its own name, which the viewer reads by its bytes."""
    try:
        with zipfile.ZipFile(source) as archive:
            large = {item.filename for item in archive.infolist()
                     if item.filename.lower().endswith(_RASTER)
                     and "/media/" in item.filename and _too_large(archive.read(item))}
    except (OSError, zipfile.BadZipFile):
        return source  # not an Office file: drawn as it is
    if not large:
        return source
    staged = workspace / "light" / source.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(
            staged, "w", compression=zipfile.ZIP_DEFLATED) as copy:
        for item in archive.infolist():
            data = archive.read(item)
            copy.writestr(item, _scaled(data, item.filename) if item.filename in large else data)
    return staged
