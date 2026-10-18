"""The public Python API, against a fake graph space: no Neo4j, no model."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import graphme
from graphme import api


def test_the_public_names_are_these() -> None:
    assert sorted(graphme.__all__) == sorted([
        "read", "GraphMe", "Settings", "load_settings", "ExtractedDocument", "BuildReport",
        "GraphSource", "GraphAnswer", "GraphNotEmbedded", "__version__",
    ])
    for name in graphme.__all__:
        assert hasattr(graphme, name)


class _Stop(Exception):
    pass


class _Space:
    opened = 0

    def __init__(self) -> None:
        _Space.opened += 1
        self.closed = False
        self.held = {"d1": "a.docx", "d2": "b.pptx"}
        self.forgotten: list[str] = []
        self.close_fails = False
        self.exported: list[Any] = []

    def corpus_rows(self, documents: Any) -> Any:
        self.exported.append(documents)
        raise _Stop

    def close(self) -> None:
        self.closed = True
        if self.close_fails:
            raise RuntimeError("connection lost")

    def document_ids(self) -> list[str]:
        return list(self.held)

    def files(self, ids: Any) -> dict[str, str]:
        return {i: self.held[i] for i in ids if i in self.held}


class _Rag:
    def __init__(self) -> None:
        self.asked: list[tuple[str, int]] = []

    def find(self, question: str, *, top_k: int) -> list[Any]:
        self.asked.append((question, top_k))
        return []

    def ask(self, question: str, *, top_k: int) -> Any:
        self.asked.append((question, top_k))
        return graphme.GraphAnswer(text="ok", sources=[])


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    made: dict[str, Any] = {"spaces": [], "rag": _Rag()}
    _Space.opened = 0

    def space(settings: Any) -> _Space:
        made["spaces"].append(_Space())
        return made["spaces"][-1]

    monkeypatch.setattr(api, "create_graph_space", space)
    monkeypatch.setattr(api, "create_graph_rag", lambda settings, space, client: made["rag"])
    monkeypatch.setattr(api, "forget_documents",
                        lambda space, ids: space.forgotten.extend(ids))
    return made


def _settings() -> graphme.Settings:
    return graphme.load_settings({"GRAPHME_PROFILE": "test", "GRAPHME_RETRIEVAL_TOP_K": "7"})


def test_one_connection_is_opened_once_and_closed_with_the_block(fakes: dict[str, Any]) -> None:
    with graphme.GraphMe(_settings()) as g:
        assert g.documents() == {"d1": "a.docx", "d2": "b.pptx"}
        g.documents()
    assert _Space.opened == 1 and fakes["spaces"][0].closed


def test_used_again_after_close_it_opens_again(fakes: dict[str, Any]) -> None:
    g = graphme.GraphMe(_settings())
    g.documents()
    g.close()
    assert g.documents()
    assert _Space.opened == 2


def test_no_top_k_is_the_settings_one(fakes: dict[str, Any]) -> None:
    with graphme.GraphMe(_settings()) as g:
        g.search("q")
        g.ask("q", top_k=3)
    assert fakes["rag"].asked == [("q", 7), ("q", 3)]


def test_deleting_an_unknown_document_deletes_nothing(fakes: dict[str, Any]) -> None:
    with graphme.GraphMe(_settings()) as g:
        with pytest.raises(KeyError, match="nope"):
            g.delete(["d1", "nope"])
        g.delete([])
        g.delete(["d2"])
        assert fakes["spaces"][0].forgotten == ["d2"]


def test_adding_a_file_of_another_kind_opens_nothing(
    fakes: dict[str, Any], tmp_path: Path
) -> None:
    other = tmp_path / "notes.txt"
    other.write_bytes(b"x")
    with graphme.GraphMe(_settings()) as g, pytest.raises(ValueError, match="unsupported"):
        g.add(other)
    assert _Space.opened == 0


def test_reading_nothing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        graphme.read(tmp_path / "missing.docx", settings=_settings())


def test_exporting_no_documents_is_not_exporting_all(
    fakes: dict[str, Any], tmp_path: Path
) -> None:
    with graphme.GraphMe(_settings()) as g:
        for chosen, passed in (([], []), (None, None), (["d1"], ["d1"])):
            with pytest.raises(_Stop):
                g.export(tmp_path, documents=chosen)
            assert fakes["spaces"][0].exported[-1] == passed


def test_reading_a_folder_says_it_takes_one_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one file, not a folder"):
        graphme.read(tmp_path, settings=_settings())


def test_deleting_a_bare_id_is_refused(fakes: dict[str, Any]) -> None:
    with graphme.GraphMe(_settings()) as g, pytest.raises(TypeError, match="list of ids"):
        g.delete("d1")  # type: ignore[arg-type]
    assert _Space.opened == 0


def test_close_resets_the_handles_even_if_the_space_will_not_close(
    fakes: dict[str, Any],
) -> None:
    g = graphme.GraphMe(_settings())
    g.documents()
    client = g._http()
    fakes["spaces"][0].close_fails = True
    with pytest.raises(RuntimeError, match="connection lost"):
        g.close()
    assert client.is_closed
    assert (g._space, g._rag, g._client) == (None, None, None)
