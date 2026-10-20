"""One document into its graph: the steps every caller runs, in the order they depend on.

The graph space and the demo both need the same graph of a document, so there is one place
that makes it. A caller that shows progress runs the steps one by one; a caller that only
wants the graph runs `build`. Nothing here is about retrieval -- passages and their vectors
are added to the graph space afterwards.

1. extract     what the file states
2. bind        each table and picture its crop, and what it encloses
3. figures     which units the page draws as one figure, and their crops widened to it
4. paragraphs  which printed pieces of what is left are one unit
5. outline     what reads under what, page by page, and a figure's parts under it
6. enrich      what each visual shows, and the other documents the pages name

A step whose part is missing is skipped: with no hierarchy provider there is no outline,
and the figures and paragraphs that only serve it are not asked for either.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from graphme.application.embedded_documents import cite_embedded
from graphme.application.hierarchy_decisions import (
    HierarchyScopeProvider,
    apply_hierarchy_decisions,
    figure_part_decisions,
    note_unread_pages,
)
from graphme.application.page_enrichment import PageEnricher
from graphme.application.xlsx_visual_binding import VisualBinder
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import ExtractedDocument, HierarchyDecision
from graphme.ports.extractor import DocumentExtractor

PageListener = Callable[[int, tuple[HierarchyDecision, ...] | None], None]
"""Hears each page's outline as it is settled: its decisions, or None when it got none."""

PhaseListener = Callable[[str], None]
"""Hears which part of the enrichment is running."""


class UnitGrouper(Protocol):
    """Joins the pieces of text each page says are one unit."""

    def group(self, *, extracted: ExtractedDocument, source: Path) -> ExtractedDocument: ...


class DocumentGraphBuilder:
    def __init__(
        self,
        *,
        extractors: Mapping[DocumentFormat, DocumentExtractor],
        visual_binder: VisualBinder | None = None,
        hierarchy_provider: HierarchyScopeProvider | None = None,
        unit_grouper: UnitGrouper | None = None,
        enricher: PageEnricher | None = None,
    ) -> None:
        self._extractors = extractors
        self._binder = visual_binder
        self._hierarchy = hierarchy_provider
        self._grouper = unit_grouper
        self._enricher = enricher

    def build(
        self,
        source: Path,
        document_format: DocumentFormat,
        *,
        on_page: PageListener | None = None,
        on_phase: PhaseListener | None = None,
    ) -> ExtractedDocument:
        """Every step, in order."""
        extracted = self.bind(self.extract(source, document_format), source)
        extracted = self.group_text(self.read_figures(extracted, source), source)
        extracted = self.read_outline(extracted, source, on_page=on_page)
        return self.drawn(self.enrich(extracted, source, document_format, on_phase=on_phase),
                          source)

    def drawn(self, extracted: ExtractedDocument, source: Path) -> ExtractedDocument:
        """Where each node is drawn written on the graph, last: a preview draws only the page
        looked at, and needs nothing else of the reading to know where things are."""
        if self._binder is None:
            return extracted
        return self._binder.drawn(extracted=extracted, source=source)

    def extract(self, source: Path, document_format: DocumentFormat) -> ExtractedDocument:
        extractor = self._extractors.get(document_format)
        if extractor is None:
            raise ValueError(f"no extractor for {document_format.value}")
        return extractor.extract(source).validate_canonical_semantic_graph()

    def bind(self, extracted: ExtractedDocument, source: Path) -> ExtractedDocument:
        """Each table and picture its crop. Before the outline is read, so the outline never
        sees a table's contents as loose units."""
        if self._binder is None:
            return extracted
        return self._binder.bind(
            extracted=extracted, source=source
        ).validate_canonical_semantic_graph()

    def read_figures(self, extracted: ExtractedDocument, source: Path) -> ExtractedDocument:
        """Which units are one figure -- asked first, so a figure can take the words written
        on it before anything reads them as text."""
        if self._hierarchy is None:
            return extracted
        figures = self._hierarchy.group_figures(extracted=extracted, source_path=str(source))
        if self._binder is None or not figures:
            return extracted
        return self._binder.widen(
            extracted=extracted, source=source, figures=figures
        ).validate_canonical_semantic_graph()

    def group_text(self, extracted: ExtractedDocument, source: Path) -> ExtractedDocument:
        """Which pieces are one paragraph -- before the outline, which places whole units or
        it places halves of them."""
        if self._hierarchy is None or self._grouper is None:
            return extracted
        return self._grouper.group(
            extracted=extracted, source=source
        ).validate_canonical_semantic_graph()

    def read_outline(
        self,
        extracted: ExtractedDocument,
        source: Path,
        *,
        on_page: PageListener | None = None,
    ) -> ExtractedDocument:
        """What reads under what. A figure's parts go under it by rule; the figure pass
        already said so, and nothing is asked again to place them."""
        if self._hierarchy is None:
            return extracted
        derived = self._hierarchy.derive(
            extracted=extracted, source_path=str(source), on_page=on_page
        )
        return note_unread_pages(
            apply_hierarchy_decisions(
                extracted, (*derived.decisions, *figure_part_decisions(extracted))
            ),
            derived.unread_pages,
        ).validate_canonical_semantic_graph()

    def enrich(
        self,
        extracted: ExtractedDocument,
        source: Path,
        document_format: DocumentFormat,
        *,
        on_phase: PhaseListener | None = None,
    ) -> ExtractedDocument:
        """What a visual shows, and a document the pages name -- neither is in the words --
        and the documents the file carries inside it, which need no model to find."""
        extracted = cite_embedded(extracted, source)
        if self._enricher is not None:
            extracted = self._enricher.enrich(
                extracted=extracted,
                source_path=str(source),
                document_format=document_format,
                on_phase=on_phase,
            )
        return extracted.validate_canonical_semantic_graph()
