"""Grouping copies and versions at the end of a build costs what changed, not the whole graph:
each document's passage hashes and signature are kept on it when it is cut, a grouping reads
the signatures and the hashes of the pairs it measures, never a passage's text, and a document
cut after a grouping (built with no embedding, or cut again) is grouped by its passages then."""
from __future__ import annotations

from typing import Any

import neo4j

from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace
from graphme.application.graph_chunks import Chunk, GraphUnit, chunks
from graphme.application.versions import VERSIONS_RULE

_GATE = [f"Rules > Gate {n}\nThe invented harbour gate {n} opens at dawn." for n in range(12)]
_EDITED = [*_GATE[:-1], "Rules > Gate 11\nThe invented harbour gate 11 opens at noon."]


class _Graph(neo4j.Driver):
    """Documents, their stamps and SIMILAR_VERSION pairs, held in memory: answers the
    statements the space sends for cutting and grouping, by what they say."""

    def __init__(self, *ids: str) -> None:
        self.docs: dict[str, dict[str, Any]] = {
            i: {"hash": f"{n:064x}", "texts": None, "keys": None, "signature": None,
                "rule": None} for n, i in enumerate(ids, start=1)}
        self.pairs: set[frozenset[str]] = set()
        self.sent: list[str] = []

    def _answer(self, query: str, p: dict[str, Any]) -> list[dict[str, Any]]:
        self.sent.append(query)
        docs = self.docs
        if "DETACH DELETE c" in query:
            docs[p["document"]]["texts"] = None
        elif "CREATE (c:" in query:
            docs[p["document"]]["texts"] = [c["text"] for c in p["chunks"]]
        elif "SET d.version_keys" in query:
            if "UNWIND" in query:
                for row in p["rows"]:
                    docs[row["id"]].update(keys=row["keys"], signature=row["signature"])
            else:
                docs[p["document"]].update(keys=p["keys"], signature=p["signature"])
                if "REMOVE d.versions_rule" in query:
                    docs[p["document"]]["rule"] = None
        elif "AS stamped" in query:
            return [{"id": i, "hash": d["hash"], "signature": d["signature"],
                     "stamped": d["rule"] == p["rule"], "cut": d["texts"] is not None,
                     "keyed": d["keys"] is not None} for i, d in docs.items()]
        elif "AS ungrouped" in query:  # the grouping before signatures were kept
            return [{"ungrouped": i} for i, d in docs.items() if d["rule"] != p["rule"]][:1]
        elif "collect(c.text)" in query:
            return [{"id": i, "hash": d["hash"], "texts": d["texts"] or []}
                    for i, d in docs.items() if "ids" not in p or i in p["ids"]]
        elif "AS keys" in query:
            return [{"id": i, "keys": docs[i]["keys"]} for i in p["ids"]]
        elif "SIMILAR_VERSION" in query and "DELETE r" in query:
            gone = set(p.get("fresh", docs))
            self.pairs = {x for x in self.pairs if not x & gone}
        elif "MERGE (a)-[:SIMILAR_VERSION]" in query:
            self.pairs |= {frozenset(x) for x in p["pairs"]}
        elif "SET d.versions_rule" in query:
            for i in p.get("fresh", docs):
                docs[i]["rule"] = p["rule"]
        return []

    def execute_query(self, query: str, parameters: dict[str, Any] | None = None,
                      **kw: Any) -> neo4j.EagerResult:
        given = {**(parameters or {}), **{k: v for k, v in kw.items() if not k.endswith("_")}}
        return neo4j.EagerResult(self._answer(query, given), None, [])  # type: ignore[arg-type]

    def session(self, **_: Any) -> Any:
        graph = self

        class _Session:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def run(self, query: str, parameters: dict[str, Any] | None = None,
                    **kw: Any) -> Any:
                return iter(graph._answer(query, {**(parameters or {}), **kw}))

            def execute_write(self, work: Any, *args: Any) -> Any:
                return work(self, *args)

        return _Session()


def _cut(space: Neo4jGraphSpace, document: str, texts: list[str]) -> None:
    space.put_chunks(document, [Chunk(member_ids=(f"{document}-u",), text=t) for t in texts],
                     rule="invented-rule")


def test_a_document_cut_after_a_grouping_joins_its_version() -> None:
    graph = _Graph("v1", "v2")
    space = Neo4jGraphSpace(graph)  # type: ignore[arg-type]
    _cut(space, "v1", _GATE)
    space.group_versions()  # v2 is in the graph, built with no embedding: not cut yet
    assert graph.docs["v2"]["rule"] is None, "a document with no passages is not stamped"

    _cut(space, "v2", _EDITED)
    space.group_versions()

    assert graph.pairs == {frozenset({"v1", "v2"})}
    assert graph.docs["v1"]["rule"] == graph.docs["v2"]["rule"] == VERSIONS_RULE


def test_a_document_cut_again_is_grouped_again() -> None:
    graph = _Graph("v1", "v2")
    space = Neo4jGraphSpace(graph)  # type: ignore[arg-type]
    _cut(space, "v1", _GATE)
    _cut(space, "v2", [f"Other > Line {n}\nAn invented recipe step {n}." for n in range(12)])
    space.group_versions()
    assert graph.pairs == set()

    _cut(space, "v2", _EDITED)  # cut again, by a new rule: the stamp goes with the old passages
    space.group_versions()

    assert graph.pairs == {frozenset({"v1", "v2"})}


def test_a_grouping_after_a_new_document_reads_no_passage_text() -> None:
    graph = _Graph("v1", "v2", "v3")
    space = Neo4jGraphSpace(graph)  # type: ignore[arg-type]
    _cut(space, "v1", _GATE)
    _cut(space, "v2", [f"Other > Line {n}\nAn invented recipe step {n}." for n in range(12)])
    space.group_versions()
    _cut(space, "v3", _EDITED)
    graph.sent.clear()

    space.group_versions()

    assert graph.pairs == {frozenset({"v1", "v3"})}
    assert [q for q in graph.sent if "c.text" in q] == []


def _handbook(edited: bool) -> list[Chunk]:
    """An invented studio handbook as the outline reading cuts it: twelve sections of four
    paragraphs, small sections sharing a passage. `edited` rewrites one paragraph longer."""
    words = ["kiln", "glaze", "shelf", "cone", "ramp", "bisque", "slip", "witness", "wash", "lid",
             "foot", "rim"]
    units = []
    for s in range(12):
        units.append(GraphUnit(f"h{s}", None, f"Section {s}", (float(s),)))
        for p in range(4):
            said = " ".join(words[(s * 7 + p * 3 + n) % 12] + str(s * 4 + p) for n in range(25))
            if edited and (s, p) == (1, 0):
                said = " ".join(["Each invented member may load three shelves a firing."] * 16)
            units.append(GraphUnit(f"h{s}p{p}", f"h{s}", said, (float(p),)))
    return chunks("Kiln handbook.docx", units)


def test_a_version_with_one_paragraph_rewritten_joins_though_its_passages_are_cut_apart() -> None:
    # One paragraph longer moves where every later passage starts: no passage of the edit is
    # the same as one of the first, though all but one of their paragraphs are.
    first, second = _handbook(edited=False), _handbook(edited=True)
    assert not {c.text for c in first} & {c.text for c in second}
    graph = _Graph("v1", "v2")
    space = Neo4jGraphSpace(graph)  # type: ignore[arg-type]
    for document, cut in (("v1", first), ("v2", second)):
        space.put_chunks(document, cut, rule="invented-rule")

    space.group_versions()

    assert graph.pairs == {frozenset({"v1", "v2"})}
