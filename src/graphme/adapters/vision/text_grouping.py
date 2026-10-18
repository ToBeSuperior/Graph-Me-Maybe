"""Ask each page which of its pieces of text are one unit, and join them.

Between the figures and the outline. A figure swallows the words written on it, so it has
to have taken them before a paragraph can be settled; and a paragraph has to be whole
before anything can be placed under it, or the outline places half a sentence.

Deciding is the page's job and joining is arithmetic -- both live in the application. What
is here is only what the pass needs from the file and the screen: a PDF's pieces are the
lines it printed, a slide's are the paragraphs of its shapes.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

from graphme.adapters.extractors.pdf import printed_pieces
from graphme.adapters.rendering.page_renderer import RendersPages
from graphme.application.unit_grouping import explode_text, group_document_units
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import ExtractedDocument
from graphme.ports.vision import VisionProvider

GROUPING_FORMATS = (DocumentFormat.PDF, DocumentFormat.PPTX)
"""Where a unit can be broken by the printing. These say which page a unit is on and where
on it, which is what a window is cut from. A document or a sheet hands over whole
paragraphs and whole cells and has no page to break them across."""


class RenderedUnitGrouper:
    """Runs the text-grouping pass over a document's rendered pages."""

    def __init__(
        self,
        *,
        renderer: RendersPages,
        vision: VisionProvider,
        limiter: AbstractContextManager[Any] | None = None,
    ) -> None:
        self._renderer = renderer
        self._vision = vision
        self._limiter = limiter if limiter is not None else nullcontext()

    def group(
        self, *, extracted: ExtractedDocument, source: Path
    ) -> ExtractedDocument:
        """The document with every unit the page says is one unit made into one."""
        document_format = extracted.document.format
        if document_format not in GROUPING_FORMATS:
            return extracted
        pieces, join = self._pieces(source, extracted, document_format)
        exploded = explode_text(extracted, pieces=pieces)
        pages = {
            page.index: page.png
            for page in self._renderer.render(source, document_format)
        }
        if not pages:
            return extracted
        with self._limiter:
            return group_document_units(
                exploded, pages=pages, vision=self._vision, join=join
            )

    @staticmethod
    def _pieces(
        source: Path, extracted: ExtractedDocument, document_format: DocumentFormat
    ) -> tuple[dict[str, tuple[str, ...]] | None, Callable[[Sequence[str]], str]]:
        """What each unit was printed in, and how its pieces go back together.

        A slide's shape arrives with its paragraphs already joined by newlines, so the
        newlines say where the pieces are and a space puts them back.
        """
        if document_format is DocumentFormat.PDF:
            return printed_pieces(source, extracted.nodes)
        return None, lambda texts: " ".join(texts)
