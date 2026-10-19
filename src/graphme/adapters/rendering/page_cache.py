"""What the renderer measured, kept beside the page images it drew (`pages.json`), and the
document's graph as binding left it (`bound.json`).

A document opened again after a restart was drawn again -- Chrome, five to seven seconds a
DOCX, PPTX or XLSX -- though its pages were already on disk: the images were kept, the
geometry beside them (cell edges, where words drew, laid-out drawings) only in memory, and the
boxes the page shows are worked out from that geometry. Kept, a document is drawn once. Its
graph as bound is kept too: reading a workbook again and cutting its crops again took longer
than the drawing did.

Kept only while nothing that made it has changed: the same file (by content), the same code
that draws, reads and binds (`code_files`), the same viewer bundle, the same browser, and every
picture the graph names still in the asset store. Anything else and it is made again, and kept
again. The server, the CLI and the words on the page are not in the key: a release changing
only those redrew every document once it was in.
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.domain.models import ExtractedDocument

FILE = "pages.json"
BOUND = "bound.json"
_PACKAGE = Path(__file__).resolve().parents[2]
_MAKERS = ("domain", "adapters/rendering", "adapters/extractors", "application")
"""The code that makes what is kept: the pages, and the graph as read and bound."""
_ASSET = re.compile(r'"(?:asset_hash|crop_asset_hash)"\s*:\s*"([0-9a-f]{64})"')


def code_files() -> list[Path]:
    return sorted(path for part in _MAKERS for path in (_PACKAGE / part).rglob("*.py"))


def _browser(chrome: str | None) -> str:
    """Which browser draws: an installed Chrome by its file and the version folders beside it
    (Windows keeps one per version), else Playwright's own Chromium by Playwright's version."""
    if chrome and Path(chrome).is_file():
        found = Path(chrome)
        stat = found.stat()
        beside = sorted(p.name for p in found.parent.iterdir())
        return f"{found}:{stat.st_size}:{stat.st_mtime_ns}:{beside}"
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"playwright {version('playwright')}"
    except PackageNotFoundError:
        return "none"


@functools.cache
def identity(viewer: Path, chrome: str | None = None) -> str:
    """What makes the pages and the graph: the code that draws, reads and binds, the viewer
    bundle's files (named by their content, and patched on top), and the browser. Asked once
    a run."""
    digest = hashlib.sha256(_browser(chrome).encode())
    for path in code_files():
        digest.update(path.read_bytes())
    for part in ("dist", "xlsx"):
        folder = viewer / part
        for path in sorted(p for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else ():
            stat = path.stat()
            digest.update(f"{path.relative_to(viewer)}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def key(content: str | None, viewer: Path, chrome: str | None = None) -> str:
    return f"{content}:{identity(viewer, chrome)}"


def save(workspace: Path, pages: tuple[RenderedPage, ...], key: str) -> None:
    """The pages' geometry beside their images (`page-NN.png`, written already)."""
    kept = [{f.name: getattr(page, f.name) for f in dataclasses.fields(page) if f.name != "png"}
            for page in pages]
    temporary = workspace / f"{FILE}.part"
    temporary.write_text(json.dumps({"key": key, "pages": kept}), encoding="utf-8")
    temporary.replace(workspace / FILE)  # whole or not at all


def load(workspace: Path, key: str) -> tuple[RenderedPage, ...] | None:
    """The pages kept under `key`, or None: none kept, kept under another key, or an image
    gone."""
    try:
        kept = json.loads((workspace / FILE).read_text(encoding="utf-8"))
        if kept.get("key") != key:
            return None
        return tuple(_page(workspace, fields) for fields in kept["pages"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _page(workspace: Path, fields: dict[str, Any]) -> RenderedPage:
    # JSON keeps a dict's keys as text and a tuple as a list: both given back as drawn.
    for edges in ("column_edges", "row_edges"):
        if fields.get(edges) is not None:
            fields[edges] = {int(k): v for k, v in fields[edges].items()}
    for many in ("text_runs", "elements"):
        fields[many] = tuple(fields.get(many) or ())
    png = (workspace / f"page-{fields['index']:02d}.png").read_bytes()
    return RenderedPage(png=png, **fields)


def save_bound(workspace: Path, bound: ExtractedDocument, key: str) -> None:
    temporary = workspace / f"{BOUND}.part"
    temporary.write_text(json.dumps({"key": key, "graph": bound.model_dump(mode="json")}),
                         encoding="utf-8")
    temporary.replace(workspace / BOUND)


def load_bound(workspace: Path, key: str, assets: LocalAssetStore) -> ExtractedDocument | None:
    """The graph as bound, kept under `key`, while every picture it names is still stored."""
    try:
        text = (workspace / BOUND).read_text(encoding="utf-8")
        kept = json.loads(text)
        if kept.get("key") != key or not assets_present(text, assets):
            return None
        return ExtractedDocument.model_validate(kept["graph"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def assets_present(text: str, assets: LocalAssetStore) -> bool:
    """Whether every asset `text` names is in the store, whatever suffix it was written under."""
    for found in set(_ASSET.findall(text)):
        folder = assets.path_for(found).parent
        if not any(folder.glob(f"{found}*")):
            return False
    return True
