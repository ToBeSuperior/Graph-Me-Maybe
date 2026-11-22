"""An answer's claims are checked against the passages it cites by one more model call; the
answer itself is never changed, and a check that fails leaves it as it was."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import neo4j
import pytest

from graphme.adapters.chat import Endpoint
from graphme.adapters.storage.answer_prompt import CHECK_SYSTEM
from graphme.adapters.storage.graph_rag import GraphRag

ANSWER = "The pressure drop limit is 0.05 MPa [Spec.docx]."
RECORD = neo4j.Record({
    "id": "c", "score": 1.0, "document_id": "a", "file": "Spec.docx",
    "text": "[Spec.docx] The pressure drop limit is 0.01 MPa.",
    "members": [], "linked": [], "modified": None})


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _claims(*claims: dict) -> str:
    return json.dumps({"claims": list(claims)})


def _rag(monkeypatch: pytest.MonkeyPatch, check: httpx.Response | str | None, *,
         check_answers: bool = True, records: list[neo4j.Record] | None = None,
         ) -> tuple[GraphRag, list[dict]]:
    """A GraphRag whose model writes ANSWER and, when asked to check it, says `check`."""
    seen: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body["messages"][0]["content"] == CHECK_SYSTEM:
            return check if isinstance(check, httpx.Response) else _reply(check or "")
        return _reply(ANSWER)

    rag = GraphRag(
        SimpleNamespace(driver=None, version_pairs=list), embedder=None,  # type: ignore[arg-type]
        endpoint=Endpoint("chat_completions", "https://chat.test/v1", "chat-test", None),  # type: ignore[arg-type]
        client=httpx.Client(transport=httpx.MockTransport(handle)), model="m", dimensions=3,
        relevance_floor=0.0, dense_weight=0.6, rrf_k=60, wait=lambda _: None,
        check_answers=check_answers)
    found = [RECORD] if records is None else records
    monkeypatch.setattr(rag, "_found", lambda question, documents, **_: found)
    return rag, seen


def test_a_claim_the_passages_do_not_hold_comes_back_with_its_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag, seen = _rag(monkeypatch, _claims({
        "text": "The pressure drop limit is 0.05 MPa", "supported": False,
        "correction": "The pressure drop limit is 0.01 MPa", "files": ["Spec.docx"]}))

    answer = rag.ask("What is the pressure drop limit?", documents=3)

    assert answer.text == ANSWER
    assert answer.checked is True
    assert answer.claims[0].supported is False
    assert answer.claims[0].correction is not None and "0.01" in answer.claims[0].correction
    assert answer.claims[0].files == ["Spec.docx"]
    # the check is given the answer and the passage it cites
    sent = json.dumps(seen[-1])
    assert "0.05 MPa" in sent and "0.01 MPa" in sent


def test_a_claim_not_written_in_the_answer_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    rag, _ = _rag(monkeypatch, _claims({
        "text": "The limit was raised last year", "supported": False,
        "correction": None, "files": []}))

    answer = rag.ask("What is the pressure drop limit?", documents=3)

    assert answer.claims == []
    assert answer.checked is True


def test_a_failed_check_leaves_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    rag, seen = _rag(monkeypatch, httpx.Response(500, text="down"))

    answer = rag.ask("What is the pressure drop limit?", documents=3)

    assert answer.text == ANSWER
    assert answer.claims == []
    assert answer.checked is False
    assert len(seen) == 2  # the answer, then the check that failed


def test_no_check_when_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    rag, seen = _rag(monkeypatch, _claims(), check_answers=False)

    answer = rag.ask("What is the pressure drop limit?", documents=3)

    assert len(seen) == 1
    assert answer.checked is False


def test_nothing_found_is_not_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    rag, seen = _rag(monkeypatch, _claims(), records=[])

    answer = rag.ask("What is the pressure drop limit?", documents=3)

    assert seen == []
    assert answer.checked is False


def test_a_blank_claim_is_dropped_and_the_valid_one_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    rag, _ = _rag(monkeypatch, _claims(
        {"text": "The pressure drop limit is 0.05 MPa", "supported": False,
         "correction": "The pressure drop limit is 0.01 MPa", "files": ["Spec.docx"]},
        {"text": "  ", "supported": True, "correction": None, "files": []}))

    answer = rag.ask("What is the pressure drop limit?", documents=3)

    assert [c.text for c in answer.claims] == ["The pressure drop limit is 0.05 MPa"]
