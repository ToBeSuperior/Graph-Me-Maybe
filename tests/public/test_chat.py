"""One chat request, written for whichever wire the provider speaks."""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "label": {"type": "string", "minLength": 1, "maxLength": 40},
    },
    "required": ["score", "label"],
}


def _ask(wire: str, answer: dict[str, Any], *, key: str | None = "k-test",
         schema: dict[str, Any] | None = SCHEMA,
         content: Any = "hello") -> tuple[str, httpx.Request]:
    from graphme.adapters.chat import Endpoint, complete

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    text = complete(client, Endpoint(wire, "https://model.test/v1", "model-test", key),  # type: ignore[arg-type]
                    system="be brief", content=content, schema=schema, name="reply")
    return text, seen[0]


CHAT = {"choices": [{"message": {"content": '{"score": 0.5, "label": "x"}'}}]}
MESSAGES = {"content": [{"type": "thinking", "thinking": ""},
                        {"type": "text", "text": '{"score": 0.5, '},
                        {"type": "text", "text": '"label": "x"}'}],
            "stop_reason": "end_turn"}


def test_chat_completions_is_the_openai_shape() -> None:
    text, request = _ask("chat_completions", CHAT)
    body = json.loads(request.content)

    assert str(request.url) == "https://model.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer k-test"
    assert body["messages"] == [{"role": "system", "content": "be brief"},
                                {"role": "user", "content": "hello"}]
    assert body["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "reply", "strict": True, "schema": SCHEMA}}
    assert text == '{"score": 0.5, "label": "x"}'


def test_no_key_sends_no_auth_header() -> None:
    _, request = _ask("chat_completions", CHAT, key=None)

    assert "authorization" not in request.headers


def test_no_schema_sends_no_response_format() -> None:
    _, request = _ask("chat_completions", CHAT, schema=None)

    assert "response_format" not in json.loads(request.content)


def test_messages_is_anthropics_shape() -> None:
    png = b"\x89PNG-invented"
    content = [{"type": "text", "text": "look"},
               {"type": "image_url", "image_url": {
                   "url": "data:image/png;base64," + base64.b64encode(png).decode(),
                   "detail": "high"}}]
    text, request = _ask("messages", MESSAGES, content=content)
    body = json.loads(request.content)

    assert str(request.url) == "https://model.test/v1/messages"
    assert request.headers["x-api-key"] == "k-test"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    assert body["system"] == "be brief"
    assert body["max_tokens"] == 16000
    assert body["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": base64.b64encode(png).decode()}}]}]
    assert body["output_config"]["format"]["type"] == "json_schema"
    sent = body["output_config"]["format"]["schema"]["properties"]
    assert sent == {"score": {"type": "number"}, "label": {"type": "string"}}
    for absent in ("temperature", "top_p", "thinking", "response_format", "output_format"):
        assert absent not in body
    assert text == '{"score": 0.5, "label": "x"}'


def test_a_jpeg_keeps_its_own_media_type() -> None:
    content = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]
    _, request = _ask("messages", MESSAGES, content=content)

    part = json.loads(request.content)["messages"][0]["content"][0]
    assert part["source"]["media_type"] == "image/jpeg"


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_a_refused_or_cut_off_answer_is_malformed(stop: str) -> None:
    with pytest.raises(RuntimeError, match="malformed response"):
        _ask("messages", {**MESSAGES, "stop_reason": stop})


def test_an_unreadable_answer_is_malformed() -> None:
    with pytest.raises(RuntimeError, match="malformed response"):
        _ask("chat_completions", {"choices": []})


def test_an_answer_with_no_text_block_is_malformed() -> None:
    with pytest.raises(RuntimeError, match="malformed response"):
        _ask("messages", {"content": [{"type": "thinking", "thinking": ""}],
                          "stop_reason": "end_turn"})


def test_the_key_never_shows_in_a_repr() -> None:
    from graphme.adapters.chat import Endpoint

    endpoint = Endpoint("chat_completions", "https://m.test/v1", "m", "k-secret")
    assert "k-secret" not in repr(endpoint)


def test_a_property_named_like_a_keyword_survives_on_the_wire() -> None:
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"maximum": {"type": "integer", "maximum": 5},
                             "maxItems": {"type": "string"}},
              "required": ["maximum", "maxItems"],
              "$defs": {"minLength": {"type": "string", "minLength": 2}}}
    _, request = _ask("messages", MESSAGES, schema=schema)

    sent = json.loads(request.content)["output_config"]["format"]["schema"]
    assert sent["properties"] == {"maximum": {"type": "integer"}, "maxItems": {"type": "string"}}
    assert sent["$defs"] == {"minLength": {"type": "string"}}


def _png_part(image: Any) -> dict[str, Any]:
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()}}


def _sent_image(content: list[dict[str, Any]]) -> tuple[str, Any, int]:
    from io import BytesIO

    from PIL import Image

    _, request = _ask("messages", MESSAGES, content=content)
    source = json.loads(request.content)["messages"][0]["content"][0]["source"]
    image = Image.open(BytesIO(base64.b64decode(source["data"])))
    return source["media_type"], image.size, len(source["data"])


def test_a_page_taller_than_anthropic_takes_is_scaled_to_fit() -> None:
    from PIL import Image

    media_type, size, _ = _sent_image([_png_part(Image.new("RGB", (900, 9000), "white"))])

    assert media_type == "image/png"
    assert max(size) == 8000
    assert size[0] == 800, "the aspect ratio is kept"


def test_a_page_too_heavy_for_anthropic_is_sent_under_five_mb() -> None:
    import random

    from PIL import Image

    noise = random.Random(7).randbytes(1600 * 1600 * 3)
    image = Image.frombytes("RGB", (1600, 1600), noise)
    part = _png_part(image)
    assert len(part["image_url"]["url"]) > 5 * 1024 * 1024, "the fixture must be over the limit"

    _, size, sent = _sent_image([part])

    assert sent <= 5 * 1024 * 1024
    assert max(size) <= 8000


def test_an_image_within_anthropics_limits_is_sent_as_it_is() -> None:
    from PIL import Image

    part = _png_part(Image.new("RGB", (1200, 1700), "white"))
    _, request = _ask("messages", MESSAGES, content=[part])

    source = json.loads(request.content)["messages"][0]["content"][0]["source"]
    assert source["data"] == part["image_url"]["url"].partition(",")[2]


def _sent_sizes(content: list[dict[str, Any]]) -> list[tuple[int, int]]:
    from io import BytesIO

    from PIL import Image

    _, request = _ask("messages", MESSAGES, content=content)
    return [Image.open(BytesIO(base64.b64decode(part["source"]["data"]))).size
            for part in json.loads(request.content)["messages"][0]["content"]
            if part["type"] == "image"]


def test_over_twenty_images_each_fit_two_thousand_pixels() -> None:
    from PIL import Image

    sizes = _sent_sizes([_png_part(Image.new("RGB", (2400, 300), "white"))] * 21)

    assert len(sizes) == 21
    assert all(size == (2000, 250) for size in sizes)


def test_twenty_images_are_sent_as_they_are() -> None:
    from PIL import Image

    part = _png_part(Image.new("RGB", (2400, 300), "white"))
    _, request = _ask("messages", MESSAGES, content=[part] * 20)

    sent = json.loads(request.content)["messages"][0]["content"]
    assert all(p["source"]["data"] == part["image_url"]["url"].partition(",")[2] for p in sent)


def test_over_a_hundred_images_is_refused_before_sending() -> None:
    from PIL import Image

    with pytest.raises(ValueError, match="100 images"):
        _ask("messages", MESSAGES, content=[_png_part(Image.new("RGB", (4, 4)))] * 101)


def test_a_request_over_the_total_size_is_refused_before_sending(monkeypatch) -> None:
    from PIL import Image

    from graphme.adapters import chat

    monkeypatch.setattr(chat, "MAX_REQUEST_BASE64", 100)
    with pytest.raises(ValueError, match="base64"):
        _ask("messages", MESSAGES, content=[_png_part(Image.new("RGB", (64, 64)))] * 2)
