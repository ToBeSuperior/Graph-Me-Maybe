"""`graphme.langchain.GraphMeLoader`: every document under the paths read, one LangChain
`Document` per passage, the passage's metadata kept as it is."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from graphme.passages import Passage


def test_each_passage_becomes_a_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from graphme import langchain

    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 invented")
    (tmp_path / "notes.txt").write_text("not a format read here")
    read: list[str] = []

    def fake_read(path: Path, *, settings: Any = None, **in_code: Any) -> str:
        read.append(Path(path).name)
        return "invented document"

    monkeypatch.setattr(langchain, "read", fake_read)
    monkeypatch.setattr(langchain, "passages", lambda document: [
        Passage("first invented passage", {"file": "a.pdf", "pages": [1]}, ("n1",)),
        Passage("second invented passage", {"file": "a.pdf", "pages": [2]}, ("n2",))])

    documents = list(langchain.GraphMeLoader(tmp_path).lazy_load())

    assert read == ["a.pdf"]
    assert [d.page_content for d in documents] == ["first invented passage",
                                                   "second invented passage"]
    assert documents[1].metadata == {"file": "a.pdf", "pages": [2], "node_ids": ["n2"]}


def test_without_the_extra_it_says_which_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    for loaded in [name for name in sys.modules if name.startswith("langchain_core.")]:
        monkeypatch.delitem(sys.modules, loaded)
    monkeypatch.setitem(sys.modules, "langchain_core", None)  # as if not installed
    monkeypatch.delitem(sys.modules, "graphme.langchain", raising=False)
    with pytest.raises(ImportError, match=r"graph-me-maybe\[langchain\]"):
        import graphme.langchain  # noqa: F401
