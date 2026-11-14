"""The golden evaluation scores each measure on its own, so a miss in one shows. Invented graphs
in the shape the server gives them."""
from __future__ import annotations

from typing import Any

import yaml

from tools.golden.evaluate import GOLDEN, evaluate


def _node(node_id: str, kind: str, text: str, **where: Any) -> dict[str, Any]:
    return {"id": node_id, "labels": ["DocNode"],
            "properties": {"type": kind, "text": text, **where}}


def _parent(parent: str, child: str) -> dict[str, Any]:
    return {"id": f"{parent}-{child}", "type": "PARENT_OF", "start": parent, "end": child,
            "properties": {}}


GOLDEN_DOC = {
    "documents": [{
        "file": "a.docx",
        "units": [{"text": "Invented body line."}, {"text": "1 | 2", "type": "table"}],
        "under": [["1. Invented heading", "Invented body line."]],
        "apart": [["1. Invented heading", "Invented body line."]],
    }],
    "links": [["a.docx", "b.docx"]],
    "questions": [{"question": "Invented?", "files": ["a.docx"], "answer": ["42"]}],
}


def _graph(parent_right: bool) -> dict[str, Any]:
    nodes = [_node("d", "document", "a"), _node("h", "text_chunk", "1. Invented heading"),
             _node("x", "text_chunk", "Elsewhere"), _node("p", "text_chunk", "Invented body line."),
             _node("t", "text_chunk", "1 | 2")]  # the table read as plain text: a mapping miss
    return {"nodes": nodes, "relationships": [
        _parent("d", "h"), _parent("d", "x"), _parent("h" if parent_right else "x", "p"),
        _parent("d", "t")]}


def _run(graph: dict[str, Any], answer: str) -> dict[str, tuple[int, int]]:
    corpus_map = {"documents": [{"id": "1", "label": "a.docx"}, {"id": "2", "label": "b.docx"}],
                  "links": [{"from": "1", "to": "2"}]}
    measures = evaluate(
        GOLDEN_DOC, {"a.docx": graph}, corpus_map,
        lambda q: {"sources": [{"file": "a.docx"}]},
        lambda q: {"answer": answer, "sources": [{"file": "b.docx"}]})
    return {m.name: (m.got, m.of) for m in measures}


def test_each_measure_is_counted_on_its_own() -> None:
    assert _run(_graph(parent_right=True), "It is 42.") == {
        "mapping": (1, 2), "split": (1, 1), "relations": (1, 1), "links": (1, 1),
        "evidence": (1, 1), "citation": (0, 1), "answer": (1, 1)}


def test_a_child_under_the_wrong_parent_misses_relations_only() -> None:
    scores = _run(_graph(parent_right=False), "No number.")

    assert scores["relations"] == (0, 1)
    assert scores["mapping"] == (1, 2)
    assert scores["answer"] == (0, 1)


def test_a_document_not_read_misses_all_of_its_measures() -> None:
    measures = evaluate(GOLDEN_DOC, {}, {"documents": [], "links": []},
                        lambda q: {"sources": []}, lambda q: {"answer": "", "sources": []})

    assert all(m.got == 0 for m in measures)


def test_the_golden_samples_name_what_the_samples_write() -> None:
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    from graphme import samples

    written = {Path_name for Path_name in _sample_names(samples)}
    named = {d["file"] for d in golden["documents"]} | {f for q in golden["questions"]
                                                        for f in q["files"]}
    assert named <= written


def _sample_names(samples: Any) -> set[str]:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as folder:
        return {path.name for path in samples.write_samples(Path(folder))}
