"""The published graph is one tree by its parent edges alone. Invented data.

The outline reads only some of a document -- what it placed under something -- and leaves the
rest where the file holds it, directly under the document. Inside, those are two edge types;
what leaves (`final_edges`) is one `PARENT_OF` per node, so a node the outline never placed
still has its parent, and every node reaches the document.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.application.final_edges import final_edges
from graphme.application.hierarchy_decisions import apply_hierarchy_decisions
from graphme.domain.enums import EdgeType, NodeType
from graphme.domain.models import HierarchyDecision


def test_every_node_reaches_the_document_by_one_parent_each(tmp_path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    for line, text in enumerate(("4. Results", "All pumps passed.", "5. Records",
                                 "Kept five years.")):
        page.insert_text((60, 80 + 60 * line), text, fontsize=12)
    document.save(tmp_path / "r.pdf")
    extracted = PDFExtractor().extract(tmp_path / "r.pdf")
    units = {n.text: n.id for n in extracted.nodes if n.type is NodeType.TEXT_CHUNK}
    # The outline placed one line; the other three it left where they were.
    read = apply_hierarchy_decisions(extracted, (HierarchyDecision(
        subject_id=units["All pumps passed."], status="parent", parent_id=units["4. Results"],
        confidence=0.9, rationale="under its heading"),))

    parents: dict[str, list[str]] = {}
    for edge in final_edges(read):
        if edge.type is EdgeType.PARENT_OF:
            parents.setdefault(edge.target_id, []).append(edge.source_id)
    root = read.document.id
    for node in read.nodes:
        if node.id == root:
            continue
        assert len(parents[node.id]) == 1
        seen, at = set(), node.id
        while at != root:
            assert at not in seen
            seen.add(at)
            at = parents[at][0]
    assert parents[units["All pumps passed."]] == [units["4. Results"]]
    assert parents[units["5. Records"]] == [root]
