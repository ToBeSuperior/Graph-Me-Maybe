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
from pathlib import Path

from graphme.adapters.extractors.pdf import printed_pieces
from graphme.adapters.rendering.page_renderer import RendersPages
from graphme.application.page_units import flow_pages
from graphme.application.unit_grouping import explode_text, group_document_units
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import ExtractedDocument
from graphme.ports.vision import VisionProvider

GROUPING_FORMATS = (DocumentFormat.PDF, DocumentFormat.PPTX, DocumentFormat.DOCX)
"""Where one thought can arrive as several units. A PDF prints lines and a slide splits a
thought over shapes. A Word file hands over whole paragraphs, but an author who wraps a
sentence by hand writes one sentence as two of them -- and at a page break the outline
read the second half as a heading of its own. A sheet's cells are values of their own."""


class RenderedUnitGrouper:
    """Runs the text-grouping pass over a document's rendered pages."""

    def __init__(
        self,
        *,
        renderer: RendersPages,
        vision: VisionProvider,
    ) -> None:
        self._renderer = renderer
        self._vision = vision

    def group(
        self,
        *,
        extracted: ExtractedDocument,
        source: Path,
    ) -> ExtractedDocument:
        """The document with every unit the page says is one unit made into one."""
        document_format = extracted.document.format
        if document_format not in GROUPING_FORMATS:
            return extracted
        pieces, join = self._pieces(source, extracted, document_format)
        # A Word paragraph is already whole: what may belong together is two of them.
        exploded = (
            extracted
            if document_format is DocumentFormat.DOCX
            else explode_text(extracted, pieces=pieces)
        )
        rendered = self._renderer.render(source, document_format)
        pages = {page.index: page.png for page in rendered}
        if not pages:
            return extracted
        return group_document_units(
            exploded,
            pages=pages,
            vision=self._vision,
            join=join,
            page_of=flow_pages(exploded, rendered, document_format),
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
