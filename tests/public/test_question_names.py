"""A document the question names is one the question's own words name."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from graphme.adapters.chat import Endpoint
from graphme.adapters.embedding import Remembered
from graphme.adapters.storage import graph_rag
from graphme.adapters.storage.graph_rag import GraphRag


@pytest.fixture(autouse=True)
def _no_splits_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_rag, "SPLITS", Remembered(256))


def _rag(split: dict[str, list[str]]) -> GraphRag:
    def handle(request: httpx.Request) -> httpx.Response:
        content = json.dumps(split)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return GraphRag(
        SimpleNamespace(driver=None), embedder=None,  # type: ignore[arg-type]
        endpoint=Endpoint("chat_completions", "https://chat.test/v1", "chat-test", None),  # type: ignore[arg-type]
        client=httpx.Client(transport=httpx.MockTransport(handle)), model="m", dimensions=3,
        relevance_floor=0.0, dense_weight=0.6, rrf_k=60,
    )


def test_a_name_copied_from_the_samples_is_not_one_the_question_names() -> None:
    """The samples that tell the split the documents' languages begin with a file name; a
    model copying that name into `documents` held the answer to a document the question never
    named. What the question names is written in the question."""
    rag = _rag({"queries": ["What is the seat leak test pressure?"],
                "documents": ["Leak Tester Inspection Checklist.xlsx", "QP-210"]})

    assert rag._asked("What is the seat leak test pressure per qp-210?")[1] == ("QP-210",)
