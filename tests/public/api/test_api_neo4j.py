"""GraphMe against the local Neo4j: run with GRAPHME_NEO4J_TEST=1. Invented documents only."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

import graphme
from graphme.domain.enums import NodeType

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHME_NEO4J_TEST") != "1", reason="needs the local Neo4j"
)


def test_documents_go_in_leave_and_export(tmp_path: Path) -> None:
    from tests.fakes.documents import units_document as _document

    tag = uuid.uuid4().hex[:8]  # hex only: the fixture hashes the id
    doc = _document(f"a{tag}", f"invented-{tag}.docx",
                    [(f"a{tag}-1", NodeType.TEXT_CHUNK, f"invented text {tag}")], [])
    with graphme.GraphMe() as g:
        g._graph_space().put_document(doc)
        try:
            assert g.documents()[f"a{tag}"] == f"invented-{tag}.docx"
            assert g.export(tmp_path / "bundle", documents=[f"a{tag}"]) == 1
            assert (tmp_path / "bundle" / "import.sh").is_file()
        finally:
            g.delete([f"a{tag}"])
        assert f"a{tag}" not in g.documents()
