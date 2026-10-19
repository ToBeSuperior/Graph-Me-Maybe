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
