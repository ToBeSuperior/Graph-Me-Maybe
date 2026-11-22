"""An edited document is read again only where it changed.

What the model said about a page is kept by what the page holds -- its units' text, kind and
place, its crops, its size -- so a page that did not change is not asked again. The headings
are asked once of the whole text, and only the page breaks beside a changed page are joined
again. Invented pages; the model is a fake that counts what it is asked.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from graphme.adapters.embedding import BatchEmbedder, KeptVectors, sweep_vectors
from graphme.adapters.rendering.pdf_pages import PdfPageRenderer
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.application import page_readings
from graphme.application.corpus_build import build_graph
from graphme.application.document_graph import DocumentGraphBuilder
from graphme.application.outline_join import JOIN_READINGS
from graphme.application.page_readings import PageReading, PageReadings, page_key
from graphme.config import Settings
from graphme.domain.enums import DocumentFormat, NodeType
from graphme.domain.models import (
    DocumentReferenceResult,
    ExtractedDocument,
    FigureGroupingResult,
    HierarchyDecision,
    HierarchyScopeResult,
    JoinPlacement,
    PageJoinRequest,
    UnitGroupingResult,
    VisualCaptionResult,
)
from graphme.runtime import graph_parts

PAGES = [
    ["Lantern Care Guide", "1. Unpacking the lantern", "Lift the lantern out by its handle."],
    ["2. Filling the tank", "Pour lamp oil up to the line on the glass."],
    ["3. Trimming the wick", "Cut the wick straight across with clean scissors."],
]


def _pdf(path: Path, pages: list[list[str]]) -> Path:
    document = pymupdf.open()
    for lines in pages:
        page = document.new_page(width=595, height=842)
        for row, line in enumerate(lines):
            page.insert_text((72, 90 + 40 * row), line, fontsize=12)
    document.save(path)
    return path


class _Model:
    """Answers every question plainly and counts each one, by question and page."""

    def __init__(self) -> None:
        self.asked: Counter[tuple[str, int]] = Counter()
        self.runs: list[set[int]] = []

    def group_page_figures(self, *, request: Any, image: Any, unit_images: Any = None
                           ) -> FigureGroupingResult:
        self.asked["figures", request.page_index] += 1
        return FigureGroupingResult()

    def group_page_units(self, *, request: Any, images: Any) -> UnitGroupingResult:
        # Short pages share one run: a run is asked, not a page.
        self.runs.append({unit.page_index for unit in request.units})
        self.asked["paragraphs", min(self.runs[-1])] += 1
        return UnitGroupingResult()

    def resolve_hierarchy_scope(self, *, request: Any, image: Any, unit_images: Any = None,
                                preceding: Any = None) -> HierarchyScopeResult:
        self.asked["outline", request.page_index] += 1
        first = request.units[0].id
        return HierarchyScopeResult(decisions=tuple(
            HierarchyDecision(subject_id=unit.id, status="root", confidence=0.9,
                              rationale="r") if unit.id == first else
            HierarchyDecision(subject_id=unit.id, status="parent", parent_id=first,
                              confidence=0.9, rationale="r")
            for unit in request.units))

    def outline_headings(self, *, lines: Any) -> tuple[tuple[str, str | None], ...]:
        self.asked["headings", 0] += 1
        return ()

    def join_page(self, *, request: PageJoinRequest) -> tuple[JoinPlacement, ...]:
        self.asked["join", request.page_index] += 1
        return tuple(JoinPlacement(subject_id=top.unit_id, place_id=request.places[0].unit_id,
                                   how="under") for top in request.tops)

    def caption_visuals(self, *, request: Any, image: Any, unit_images: Any = None
                        ) -> VisualCaptionResult:
        self.asked["captions", request.page_index] += 1
        return VisualCaptionResult(captions=())

    def find_references(self, *, request: Any, image: Any, unit_images: Any = None
                        ) -> DocumentReferenceResult:
        self.asked["references", request.page_index] += 1
        return DocumentReferenceResult(references=())

    def read_scanned_page(self, image: bytes) -> Any:
        raise AssertionError("no scanned page here")

    def is_a_table(self, image: bytes) -> bool:
        return False

    def by(self, question: str) -> set[int]:
        return {page for (asked, page), _ in self.asked.items() if asked == question}

    def count(self, question: str) -> int:
        return sum(n for (asked, _), n in self.asked.items() if asked == question)


def _builder(model: _Model, folder: Path, salt: str = "invented-model") -> DocumentGraphBuilder:
    parts = graph_parts(Settings(profile="test", data_dir=folder), renderer=PdfPageRenderer(),
                        vision=model, assets=LocalAssetStore(folder / "assets"),
                        readings=PageReadings(folder / "readings", salt=salt))
    return DocumentGraphBuilder(**parts)


def _parents(extracted: ExtractedDocument) -> dict[str, str]:
    """Each unit's parent in the outline, by text: ids change with the file's content."""
    text = {node.id: node.text or node.label for node in extracted.nodes}
    return {text[e.target_id]: text[e.source_id] for e in extracted.edges
            if e.type.value == "OUTLINE_PARENT"}


def _texts(extracted: ExtractedDocument) -> list[str]:
    return sorted(n.text or "" for n in extracted.nodes if n.type is NodeType.TEXT_CHUNK)


def test_an_edited_page_is_the_only_page_asked_again(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "lantern.pdf", PAGES)
    first = _Model()
    _builder(first, tmp_path).build(source, DocumentFormat.PDF)
    assert first.by("outline") == {1, 2, 3}

    edited = [PAGES[0], ["2. Filling the tank", "Pour lamp oil up to the red line."], PAGES[2]]
    again = _Model()
    built = _builder(again, tmp_path).build(_pdf(source, edited), DocumentFormat.PDF)

    for question in ("figures", "outline", "references"):
        assert again.by(question) == {2}, question
    assert again.runs and all(2 in run for run in again.runs)  # only runs holding the edit
    assert again.count("headings") == 1
    # The two page breaks beside the edited page, each read as a break always is.
    assert {k: n for k, n in again.asked.items() if k[0] == "join"} == {
        ("join", 2): JOIN_READINGS, ("join", 3): JOIN_READINGS}
    assert built.document.metadata["pages_read"] == 1
    assert built.document.metadata["pages_reused"] == 2

    # What is kept reads the same as what was asked: the edited text, the same outline.
    fresh = _Model()
    asked = _builder(fresh, tmp_path / "elsewhere").build(source, DocumentFormat.PDF)
    assert _texts(built) == _texts(asked)
    assert _parents(built) == _parents(asked)


def test_the_same_file_built_again_asks_nothing(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "lantern.pdf", PAGES)
    _builder(_Model(), tmp_path).build(source, DocumentFormat.PDF)

    again = _Model()
    built = _builder(again, tmp_path).build(source, DocumentFormat.PDF)

    assert again.asked == Counter()
    assert built.document.metadata["pages_read"] == 0
    assert built.document.metadata["pages_reused"] == 3


def test_a_new_reading_rule_asks_every_page_again(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    source = _pdf(tmp_path / "lantern.pdf", PAGES)
    first = _Model()
    _builder(first, tmp_path).build(source, DocumentFormat.PDF)

    monkeypatch.setattr(page_readings, "READING_RULE", page_readings.READING_RULE + 1)
    again = _Model()
    _builder(again, tmp_path).build(source, DocumentFormat.PDF)

    for question in ("figures", "outline", "references"):
        assert again.by(question) == {1, 2, 3}, question
    assert again.runs == first.runs
    assert again.count("headings") == 1
    assert again.by("join") == {2, 3}


def test_a_page_key_is_its_content_and_the_rule() -> None:
    units = [("Lift the lantern out.", "TEXT_CHUNK", [0.1, 0.1, 0.9, 0.2])]
    key = page_key(units, ["c" * 64], (595, 842))
    assert key == page_key(units, ["c" * 64], (595, 842))
    assert key != page_key([("Lift the kettle out.", *units[0][1:])], ["c" * 64], (595, 842))
    assert key != page_key(units, ["d" * 64], (595, 842))
    assert key != page_key(units, ["c" * 64], (842, 595))
    assert key != page_key(units, ["c" * 64], (595, 842), page_readings.READING_RULE + 1)


def test_readings_no_graph_document_names_are_swept(tmp_path: Path) -> None:
    readings = PageReadings(tmp_path)
    readings.put("k-kept", PageReading(names=("a",), answer={"said": "a"}))
    readings.put("k-gone", PageReading(names=("b",), answer={"said": "b"}))
    readings.name("1" * 64, "k-kept")
    readings.name("2" * 64, "k-gone")
    readings.settle("1" * 64, "1" * 64)
    readings.settle("2" * 64, "2" * 64)

    assert readings.documents() == {"1" * 64, "2" * 64}
    readings.sweep({"1" * 64}, before=float("inf"))

    assert readings.get("k-kept") is not None and readings.get("k-gone") is None
    assert readings.documents() == {"1" * 64}


def test_a_build_reports_the_pages_read_and_reused(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "lantern.pdf", PAGES)
    builder = _builder(_Model(), tmp_path)
    builder.build(source, DocumentFormat.PDF)

    class _Space:
        def contents(self, hashes: list[str]) -> set[str]:
            return set()

        def put_document(self, extracted: ExtractedDocument) -> None:
            pass

        def read_from(self, source_path: str) -> set[str]:
            return set()

        def identities(self) -> tuple[()]:
            return ()

        def mentions(self) -> tuple[()]:
            return ()

        def unlinked(self, rule: str) -> set[str]:
            return set()

        def put_links(self, links: list[Any], *, of: list[Any]) -> None:
            pass

        def mark_linked(self, documents: list[str], rule: str) -> None:
            pass

    report = build_graph([source], process=lambda path: builder.build(path, DocumentFormat.PDF),
                         space=_Space(), again=True)  # type: ignore[arg-type]

    assert (report.pages_read, report.pages_reused) == (0, 3)


class _Embedder(BatchEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 1.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.sent += texts
        return [[float(len(text)), 0.5] for text in texts]


def test_a_passage_embedded_before_is_not_embedded_again(tmp_path: Path) -> None:
    inner = _Embedder()
    kept = KeptVectors(inner, tmp_path, model="invented-embedder")

    assert kept.embed_texts(["Lift the lantern.", "Fill the tank."]) == [[17.0, 0.5],
                                                                          [14.0, 0.5]]
    again = KeptVectors(inner, tmp_path, model="invented-embedder")
    assert again.embed_texts(["Fill the tank.", "Trim the wick."]) == [[14.0, 0.5],
                                                                       [14.0, 0.5]]
    assert inner.sent == ["Lift the lantern.", "Fill the tank.", "Trim the wick."]

    other = KeptVectors(inner, tmp_path, model="another-embedder")
    other.embed_texts(["Fill the tank."])
    assert inner.sent[-1] == "Fill the tank."  # another model's vector is its own


def _pages_with_a_picture(path: Path, colour: tuple[int, int, int]) -> Path:
    from io import BytesIO

    from PIL import Image

    document = pymupdf.open()
    for number, lines in enumerate(PAGES, start=1):
        page = document.new_page(width=595, height=842)
        for row, line in enumerate(lines):
            page.insert_text((72, 90 + 40 * row), line, fontsize=12)
        if number == 2:
            picture = BytesIO()
            Image.new("RGB", (120, 90), colour).save(picture, format="PNG")
            page.insert_image(pymupdf.Rect(72, 300, 312, 480), stream=picture.getvalue())
    document.save(path)
    return path


def test_another_model_reads_every_page_again(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "lantern.pdf", PAGES)
    _builder(_Model(), tmp_path, salt="invented-model").build(source, DocumentFormat.PDF)

    again = _Model()
    _builder(again, tmp_path, salt="another-model").build(source, DocumentFormat.PDF)

    assert again.by("outline") == {1, 2, 3} and again.count("headings") == 1


def test_a_picture_redrawn_asks_its_page_again(tmp_path: Path) -> None:
    source = _pages_with_a_picture(tmp_path / "lantern.pdf", (200, 40, 40))
    _builder(_Model(), tmp_path).build(source, DocumentFormat.PDF)

    again = _Model()
    _builder(again, tmp_path).build(
        _pages_with_a_picture(source, (40, 40, 200)), DocumentFormat.PDF)

    assert again.by("outline") == {2}
    assert again.by("captions") == {2}


def test_units_moved_on_a_page_ask_that_page_again(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "lantern.pdf", PAGES)
    _builder(_Model(), tmp_path).build(source, DocumentFormat.PDF)

    moved = [PAGES[0], list(reversed(PAGES[1])), PAGES[2]]
    again = _Model()
    _builder(again, tmp_path).build(_pdf(source, moved), DocumentFormat.PDF)
    assert again.by("outline") == {2}

    added = [PAGES[0], [*PAGES[1], "Wipe the glass dry."], PAGES[2]]
    third = _Model()
    _builder(third, tmp_path).build(_pdf(source, added), DocumentFormat.PDF)
    assert third.by("outline") == {2}


def test_two_pages_alike_each_keep_their_own_units(tmp_path: Path) -> None:
    alike = [PAGES[1], PAGES[1], PAGES[2]]
    built = _builder(_Model(), tmp_path).build(_pdf(tmp_path / "twice.pdf", alike),
                                               DocumentFormat.PDF)

    # The second page took the first page's reading, in its own units' ids.
    assert built.document.metadata["pages_reused"] == 1
    page = {node.id: node.locator.page for node in built.nodes}
    parent = {e.target_id: e.source_id for e in built.edges
              if e.type.value == "OUTLINE_PARENT"}
    lines = [n.id for n in built.nodes if n.text == PAGES[1][1]]
    assert sorted(page[u] for u in lines) == [1, 2]
    assert all(page[parent[u]] == page[u] for u in lines)


def test_a_reading_cut_short_is_asked_again(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "lantern.pdf", PAGES)
    _builder(_Model(), tmp_path).build(source, DocumentFormat.PDF)
    for kept in (tmp_path / "readings").glob("*.json"):
        kept.write_text(kept.read_text(encoding="utf-8")[:20], encoding="utf-8")

    again = _Model()
    _builder(again, tmp_path).build(source, DocumentFormat.PDF)

    assert again.by("outline") == {1, 2, 3}


def test_a_print_read_keeps_its_readings_under_the_file_it_was_printed_from(
        tmp_path: Path) -> None:
    # A Hangul document is read as the PDF it prints to, and the graph holds it by the hash
    # of the file given: its readings are named by that hash, or a sweep takes them.
    from graphme.adapters.rendering.hwp_print import as_original

    original = tmp_path / "lantern.hwp"
    original.write_bytes(b"invented hangul document")
    printed = _pdf(tmp_path / "printed.pdf", PAGES)
    readings = PageReadings(tmp_path / "readings", salt="invented-model")
    parts = graph_parts(Settings(profile="test", data_dir=tmp_path), renderer=PdfPageRenderer(),
                        vision=_Model(), assets=LocalAssetStore(tmp_path / "assets"),
                        readings=readings)
    builder = DocumentGraphBuilder(**parts)

    class _Space:
        def contents(self, hashes: list[str]) -> set[str]:
            return set()

        def put_document(self, extracted: ExtractedDocument) -> None:
            self.held = extracted.document.content_hash

        def read_from(self, source_path: str) -> set[str]:
            return set()

        def identities(self) -> tuple[()]:
            return ()

        def mentions(self) -> tuple[()]:
            return ()

        def unlinked(self, rule: str) -> set[str]:
            return set()

        def put_links(self, links: list[Any], *, of: list[Any]) -> None:
            pass

        def mark_linked(self, documents: list[str], rule: str) -> None:
            pass

    space = _Space()
    build_graph([original], process=lambda _: as_original(
        builder.build(printed, DocumentFormat.PDF), original), space=space,  # type: ignore[arg-type]
        again=True, readings=readings)

    assert readings.documents() == {space.held}
    readings.sweep({space.held}, before=float("inf"))
    again = _Model()
    _builder(again, tmp_path).build(printed, DocumentFormat.PDF)
    assert again.asked == Counter()


def test_a_build_names_only_the_readings_it_used(tmp_path: Path) -> None:
    readings = PageReadings(tmp_path)
    readings.put("old", PageReading(names=("a",), answer={}))
    readings.put("new", PageReading(names=("a",), answer={}))
    readings.name("d" * 64, "old")
    readings.settle("d" * 64, "d" * 64)
    readings.name("d" * 64, "new")
    readings.name("d" * 64, "new")
    readings.settle("d" * 64, "d" * 64)
    (tmp_path / "stray.part").write_text("cut short")

    readings.sweep({"d" * 64}, before=float("inf"))

    assert readings.get("new") is not None and readings.get("old") is None
    assert not (tmp_path / "stray.part").exists()


def test_vectors_no_passage_holds_are_swept(tmp_path: Path) -> None:
    kept = KeptVectors(_Embedder(), tmp_path, model="invented-embedder", dimensions=2)
    kept.embed_texts(["Fill the tank.", "Trim the wick."])
    (tmp_path / "stray.part").write_bytes(b"")

    sweep_vectors(tmp_path, model="invented-embedder", texts=["Fill the tank."],
                  before=float("inf"))

    inner = _Embedder()
    again = KeptVectors(inner, tmp_path, model="invented-embedder", dimensions=2)
    again.embed_texts(["Fill the tank.", "Trim the wick."])
    assert inner.sent == ["Trim the wick."]
    assert not (tmp_path / "stray.part").exists()


def test_a_vector_of_another_size_is_embedded_again(tmp_path: Path) -> None:
    KeptVectors(_Embedder(), tmp_path, model="invented-embedder", dimensions=2).embed_texts(
        ["Fill the tank."])
    inner = _Embedder()
    KeptVectors(inner, tmp_path, model="invented-embedder", dimensions=3).embed_texts(
        ["Fill the tank."])
    assert inner.sent == ["Fill the tank."]


def test_readings_are_kept_before_a_model_key_is_given(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Where readings go is named by the model, not its key: a build with no key yet still
    finds its readings (and says the missing key at its first call, not here)."""
    from graphme import model_endpoint, runtime

    monkeypatch.setattr(model_endpoint, "KEY_REQUIRED", True)
    settings = Settings(profile="test", data_dir=tmp_path, api_key=None, model="model-a")
    kept = runtime.page_readings(settings)
    kept.put("k", PageReading(names=("a",), answer={"said": "a"}))
    assert kept.get("k") is not None
    other = runtime.page_readings(Settings(profile="test", data_dir=tmp_path, api_key=None,
                                           model="model-b"))
    assert other.get("k") is None
