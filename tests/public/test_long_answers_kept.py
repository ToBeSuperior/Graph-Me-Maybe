"""An answer longer than a field holds is cut to fit, not thrown away with the call.

A caption is asked to transcribe as much as a visual prints, and a field has a cap the
model is never shown; over it, the whole page's captions were refused, asked again and
lost.
"""
from __future__ import annotations

import json
from io import BytesIO

import httpx
from PIL import Image

from graphme.adapters.vision.chat_completions import ChatCompletionsVLMAdapter
from graphme.domain.enums import NodeType
from graphme.domain.models import CAPTION_CHARS, HierarchyUnit, VisualCaptionRequest


def _image() -> bytes:
    out = BytesIO()
    Image.new("RGB", (120, 80), "white").save(out, format="PNG")
    return out.getvalue()


def test_a_caption_longer_than_its_field_is_cut_to_fit_and_asked_once() -> None:
    request = VisualCaptionRequest(
        request_id="r", document_id="doc", scope="page-01", page_index=1, under={},
        units=(HierarchyUnit(id="table-1", label="Table A1:C9", scope="page-01", order=1,
                             node_type=NodeType.TABLE),),
    )
    calls: list[int] = []

    def handle(http: httpx.Request) -> httpx.Response:
        calls.append(1)
        prompt = json.loads(json.loads(http.content)["messages"][1]["content"][0]["text"])
        answer = {"captions": [{"subject": prompt["units"][0]["id"],
                                "caption": "pump row 7 " * (CAPTION_CHARS // 5),
                                "confidence": 0.8, "decorative": False}]}
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(answer)}}]})

    adapter = ChatCompletionsVLMAdapter(base_url="http://127.0.0.1:9/v1", model="m",
                                        client=httpx.Client(transport=httpx.MockTransport(handle)))
    result = adapter.caption_visuals(request=request, image=_image())

    assert len(calls) == 1
    [caption] = result.captions
    assert caption.caption.startswith("pump row 7")
    assert len(caption.caption) <= CAPTION_CHARS


def test_a_group_joining_two_units_settled_apart_is_dropped_alone() -> None:
    """The window before settled the pump line and the valve line as two units; a group
    carrying both on says that window was wrong about both. That group goes, and the rest
    of the window's answer stays -- refused whole, the window was asked again and lost."""
    from graphme.domain.models import GroupableUnit, UnitGroupingRequest

    texts = {"a": "Pump checked", "b": "Valve sealed", "c": "and greased.",
             "d": "Hose fitted", "e": "at both ends."}
    request = UnitGroupingRequest(
        request_id="r", document_id="doc", scope="page-02",
        units=tuple(GroupableUnit(id=u, text=t, node_type=NodeType.TEXT_CHUNK, page_index=2)
                    for u, t in texts.items()),
        settled={"a": "g1", "b": "g2"},
    )
    calls: list[int] = []

    def handle(http: httpx.Request) -> httpx.Response:
        calls.append(1)
        prompt = json.loads(json.loads(http.content)["messages"][1]["content"][0]["text"])
        ids = [piece["id"] for piece in prompt["pieces"]]
        answer = {"groups": [{"pieces": ids[0:3]}, {"pieces": ids[3:5]}]}
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(answer)}}]})

    adapter = ChatCompletionsVLMAdapter(base_url="http://127.0.0.1:9/v1", model="m",
                                        client=httpx.Client(transport=httpx.MockTransport(handle)))
    result = adapter.group_page_units(request=request, images=[_image()])

    assert len(calls) == 1
    assert {label.unit_id for label in result.labels} == {"d", "e"}
