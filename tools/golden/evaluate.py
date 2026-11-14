"""Score a build of the invented samples against their expected graph (`samples.yaml`), each
measure on its own: mapping and layout (units found, of their kind, where they are), split
(texts kept apart), relations (each child under its parent), links between documents, evidence
recall (the files a question must find), citation (the answer cites them) and the answer (it
holds the words). A miss in one is not hidden by a hit in another.

    python tools/golden/evaluate.py <server url> [--workspace golden]

Builds the samples into a workspace of their own on that server (`graphme serve`, the app or
the dev server), which calls the configured model: run it only where that is allowed.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

GOLDEN = Path(__file__).with_name("samples.yaml")

Graph = Mapping[str, Any]
"""A document's graph as `GET /v1/documents/{id}/graph` gives it: nodes and relationships."""


@dataclass
class Measure:
    name: str
    got: int = 0
    of: int = 0
    misses: list[str] = field(default_factory=list)

    def count(self, hit: bool, what: str) -> None:
        self.of += 1
        if hit:
            self.got += 1
        else:
            self.misses.append(what)

    def line(self) -> str:
        share = f"{self.got / self.of:.0%}" if self.of else "-"
        return f"{self.name:<12} {self.got:>3}/{self.of:<3} {share:>5}"


def _squashed(text: str) -> str:
    return re.sub(r"\s+", "", text or "").casefold()


def _text(node: Mapping[str, Any]) -> str:
    properties = node["properties"]
    return str(properties.get("text") or properties.get("label") or "")


def holding(graph: Graph, text: str) -> list[Mapping[str, Any]]:
    """The units whose text holds `text`, spaces aside; the document node is not a unit."""
    wanted = _squashed(text)
    return [node for node in graph["nodes"]
            if node["properties"].get("type") != "document" and wanted in _squashed(_text(node))]


def _parents(graph: Graph) -> dict[str, str]:
    return {r["end"]: r["start"] for r in graph["relationships"] if r["type"] == "PARENT_OF"}


def score_document(expected: Mapping[str, Any], graph: Graph,
                   measures: dict[str, Measure]) -> None:
    file = expected["file"]
    for unit in expected.get("units", ()):
        found = [node for node in holding(graph, unit["text"])
                 if ("type" not in unit or node["properties"].get("type") == unit["type"])
                 and ("sheet" not in unit or node["properties"].get("sheet") == unit["sheet"])
                 and ("slide" not in unit or node["properties"].get("slide") == unit["slide"])]
        measures["mapping"].count(bool(found), f"{file}: {unit['text']}")
    parents = _parents(graph)
    nodes = {node["id"]: node for node in graph["nodes"]}
    for parent, child in expected.get("under", ()):
        under = any(
            (above := nodes.get(parents.get(node["id"], ""))) is not None
            and _squashed(parent) in _squashed(_text(above))
            for node in holding(graph, child))
        measures["relations"].count(under, f"{file}: {child!r} under {parent!r}")
    units = [node for node in graph["nodes"] if node["properties"].get("type") != "document"]
    for texts in expected.get("apart", ()):
        together = any(all(_squashed(t) in _squashed(_text(node)) for t in texts)
                       for node in units)
        read = all(any(_squashed(t) in _squashed(_text(node)) for node in units)
                   for t in texts)  # a text not read at all is no split kept
        measures["split"].count(read and not together,
                                f"{file}: {' / '.join(texts)} in one unit, or not read")


def score_links(expected: list[list[str]], corpus_map: Mapping[str, Any],
                measures: dict[str, Measure]) -> None:
    names = {d["id"]: d["label"] for d in corpus_map["documents"]}
    linked = {(names.get(link["from"]), names.get(link["to"])) for link in corpus_map["links"]}
    for source, target in expected:
        measures["links"].count((source, target) in linked, f"{source} -> {target}")


def score_question(expected: Mapping[str, Any], found: Mapping[str, Any],
                   answered: Mapping[str, Any], measures: dict[str, Measure]) -> None:
    question = expected["question"]
    found_files = {source["file"] for source in found["sources"]}
    cited = {source["file"] for source in answered["sources"]}
    for file in expected["files"]:
        measures["evidence"].count(file in found_files, f"{question} -> {file}")
        measures["citation"].count(file in cited, f"{question} cites {file}")
    holds = all(_squashed(words) in _squashed(answered["answer"]) for words in expected["answer"])
    measures["answer"].count(holds, f"{question} -> {' / '.join(expected['answer'])}")


MEASURES = ("mapping", "split", "relations", "links", "evidence", "citation", "answer")


def evaluate(golden: Mapping[str, Any], graphs: Mapping[str, Graph],
             corpus_map: Mapping[str, Any], search: Callable[[str], Mapping[str, Any]],
             ask: Callable[[str], Mapping[str, Any]]) -> list[Measure]:
    """Every measure over one build: `graphs` by file name, `search` and `ask` a question."""
    measures = {name: Measure(name) for name in MEASURES}
    for expected in golden["documents"]:
        graph = graphs.get(expected["file"])
        if graph is None:  # not read at all: every one of its measures missed
            graph = {"nodes": [], "relationships": []}
        score_document(expected, graph, measures)
    score_links(golden.get("links", []), corpus_map, measures)
    for expected in golden.get("questions", ()):
        score_question(expected, search(expected["question"]), ask(expected["question"]),
                       measures)
    return list(measures.values())


def _until(check: Callable[[], Any], seconds: float) -> Any:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if got := check():
                return got
        except httpx.HTTPError:
            pass
        time.sleep(3)
    raise TimeoutError("the server did not get there in time")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("server")
    parser.add_argument("--workspace", default="golden")
    options = parser.parse_args(argv)
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    base = options.server.rstrip("/")
    here = {"Origin": base}
    with httpx.Client(base_url=base, timeout=900) as client, \
            tempfile.TemporaryDirectory(prefix="graphme-golden-") as folder:
        listed = client.get("/v1/workspaces").json()["items"]
        space = next((w["id"] for w in listed if w["name"] == options.workspace), None)
        if space is None:
            space = client.post("/v1/workspaces", json={"name": options.workspace},
                                headers=here).json()["id"]
        client.post(f"/v1/workspaces/{space}/open", headers=here)
        _until(lambda: client.get("/v1/documents").status_code == 200, 600)

        from graphme.samples import write_samples
        write_samples(Path(folder))
        job = _until(lambda: (b := client.post("/v1/jobs/build", json={"folder": folder}).json())
                     .get("id") and b, 600)
        job = _until(lambda: (j := client.get(f"/v1/jobs/{job['id']}").json())["state"]
                     in ("done", "error", "cancelled") and j, 3600)
        print(f"build: {job['state']} {job.get('error') or ''}")

        items = client.get("/v1/documents").json()["items"]
        graphs = {item["name"]: client.get(f"/v1/documents/{item['document_id']}/graph").json()
                  for item in items if item.get("document_id")}
        measures = evaluate(
            golden, graphs, client.get("/v1/graph/map").json(),
            lambda q: client.post("/v1/search", json={"question": q}).json(),
            lambda q: client.post("/v1/ask", json={"question": q}).json())
    for measure in measures:
        print(measure.line())
        for miss in measure.misses:
            print(f"    miss: {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
