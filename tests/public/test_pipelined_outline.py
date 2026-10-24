"""The outline is read on the paragraphs the grouping joined, never on the pieces before.

A page that was read before its last line was joined to the next page's first had a decision
about a piece that was gone, and the server read a slide on its pieces while the command line
read it on its paragraphs. Every format is grouped first, then read.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from graphme.application.document_graph import DocumentGraphBuilder
from graphme.application.unit_grouping import apply_groups
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import HierarchyDerivation, UnitLabel
from tests.fakes.documents import named_document

REPORT = named_document("d", "report.pdf", title="Results")


class _Joins:
    """Joins the body to the line after it."""

    def group(self, *, extracted: Any, source: Path) -> Any:
        return apply_groups(extracted, (UnitLabel(unit_id="d-body", group="p", order=1),
                                        UnitLabel(unit_id="d-page", group="p", order=2)))


class _Sees:
    """Records the units the outline was asked about; reads nothing."""

    def __init__(self) -> None:
        self.saw: set[str] = set()

    def derive(self, *, extracted: Any, source_path: str,
               on_page: Any = None) -> HierarchyDerivation:
        self.saw = {n.id for n in extracted.nodes}
        return HierarchyDerivation()


@pytest.mark.parametrize("document_format", [DocumentFormat.PDF, DocumentFormat.PPTX])
def test_the_outline_is_asked_about_the_joined_paragraphs(
    document_format: DocumentFormat,
) -> None:
    document = REPORT.model_copy(update={"document": REPORT.document.model_copy(
        update={"format": document_format})})
    outline = _Sees()
    builder = DocumentGraphBuilder(extractors={}, hierarchy_provider=outline,  # type: ignore[arg-type]
                                   unit_grouper=_Joins())
    heard: list[Any] = []

    builder.group_and_read_outline(document, Path("report"), on_grouped=heard.append)

    assert "d-page" not in outline.saw
    assert len(heard) == 1 and "d-page" not in {n.id for n in heard[0].nodes}
