"""An answer that is not JSON at all is a slip in the answer, retried like a missing field."""

from __future__ import annotations

import json
from io import BytesIO

import httpx
from PIL import Image

from graphme.adapters.vision.chat_completions import ChatCompletionsVLMAdapter
from graphme.domain.enums import NodeType
from graphme.domain.ids import sha256_bytes
from graphme.domain.models import HierarchyImageDescriptor, HierarchyScopeRequest, HierarchyUnit


def _page() -> bytes:
    output = BytesIO()
    Image.new("RGB", (120, 80), "white").save(output, format="PNG")
    return output.getvalue()


def _request(image: bytes) -> HierarchyScopeRequest:
    return HierarchyScopeRequest(
        request_id="scope-request",
        document_id="doc",
        scope="page:1",
        page_index=1,
        scope_root_id="doc",
        units=(
            HierarchyUnit(
                id="subject", label="Visible text", scope="page:1", order=1,
                node_type=NodeType.TEXT_CHUNK,
            ),
        ),
        image=HierarchyImageDescriptor(
            asset_hash=sha256_bytes(image), size_bytes=len(image), width=120, height=80,
            document_id="doc", scope="page:1", page_index=1, covered_unit_ids=("subject",),
        ),
    )


def _answer(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_an_answer_that_is_not_json_is_asked_again() -> None:
    image = _page()
    calls: list[int] = []
    decided = json.dumps({"decisions": [{
        "subject": "subject", "status": "root", "parent": None, "confidence": 0.9,
        "rationale": "One unit.",
    }]})

    def handle(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _answer("Sure! Here is the outline you asked for" if len(calls) == 1 else decided)

    adapter = ChatCompletionsVLMAdapter(
        base_url="http://127.0.0.1:8000/v1", model="vlm-test",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    result = adapter.resolve_hierarchy_scope(request=_request(image), image=image)

    assert len(calls) == 2
    assert result.decisions


def test_a_request_the_endpoint_refuses_is_the_page_unanswered_saying_why() -> None:
    """A 400 is this page's request turned down -- an image the provider will not take, say.
    It is the page going unanswered, as a malformed answer is, so the caller leaves this page
    and reads the rest; it is not the whole document failing, and the reason is kept."""
    image = _page()
    calls: list[int] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "invented: image not accepted"}})

    adapter = ChatCompletionsVLMAdapter(
        base_url="http://127.0.0.1:8000/v1", model="vlm-test",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    try:
        adapter.resolve_hierarchy_scope(request=_request(image), image=image)
    except RuntimeError as error:
        said = f"{error} {error.__cause__}"
    else:
        raise AssertionError("a refused page has no answer")

    assert "image not accepted" in said
    assert len(calls) > 1, "asked again: a refusal can be the provider's moment, not the page"


def test_a_key_the_endpoint_refuses_still_stops_everything() -> None:
    """A 401 says nothing about a page: every page would get it, so it is not skipped."""
    image = _page()
    adapter = ChatCompletionsVLMAdapter(
        base_url="http://127.0.0.1:8000/v1", model="vlm-test",
        client=httpx.Client(transport=httpx.MockTransport(
            lambda _request: httpx.Response(401, json={"error": {"message": "bad key"}}))),
    )
    try:
        adapter.resolve_hierarchy_scope(request=_request(image), image=image)
    except httpx.HTTPStatusError as error:
        assert error.response.status_code == 401
    else:
        raise AssertionError("a refused key is not a page left unread")
