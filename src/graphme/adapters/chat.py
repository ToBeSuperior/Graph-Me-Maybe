"""One request to a chat model, written for whichever wire its provider speaks.

Every model call goes through `complete`: OpenAI's chat completions (OpenAI, Gemini's
compatible layer, any server speaking it) or Anthropic's Messages API. Callers write their
content in the chat-completions shape (text parts, `image_url` parts carrying a data URL);
the messages wire translates it here, and nowhere else.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import httpx
from PIL import Image

from graphme.adapters.strict_schema import portable
from graphme.providers import Wire

MALFORMED = "the model endpoint returned a malformed response"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 16_000
# ponytail: fixed at 16000; stream with a setting if one page's output overflows it
_CUT_SHORT = frozenset({"max_tokens", "refusal"})
MAX_IMAGE_SIDE = 8000
MAX_IMAGE_BASE64 = 5 * 1024 * 1024
"""What the Messages API takes for one image: pixels on a side, and base64 characters."""
MANY_IMAGES = 20
MANY_IMAGES_SIDE = 2000
"""Past `MANY_IMAGES` in one request, the Messages API takes each at most this on a side."""
MAX_IMAGES = 100
MAX_REQUEST_BASE64 = 32 * 1024 * 1024
"""The most images, and base64 characters of them, one Messages request may carry."""

Content = str | list[dict[str, Any]]

TRANSIENT_STATUS = frozenset({408, 429, 502, 503, 504, 529})
"""Endpoint answers that say nothing about the request (529 is Anthropic's "overloaded").
A caller retries them on its own schedule, counted apart from the attempts a malformed
answer gets."""


def is_transient(error: httpx.HTTPError) -> bool:
    """A timeout or a dropped connection always; a status only when it says "not now"."""
    if not isinstance(error, httpx.HTTPStatusError):
        return True
    return error.response.status_code in TRANSIENT_STATUS


REFUSED_STATUS = frozenset({400, 413, 422})
"""Endpoint answers turning this one request down -- its content, its size. Another page is
not turned down for it, so the page goes unanswered and the rest are read. A refused key or
a missing model (401, 403, 404) would turn every page down, and stops the reading."""


def refused(error: httpx.HTTPError) -> bool:
    return (isinstance(error, httpx.HTTPStatusError)
            and error.response.status_code in REFUSED_STATUS)


def image_part(png: bytes) -> dict[str, Any]:
    """A PNG as one part of a chat message."""
    return {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")},
    }


@dataclass(frozen=True, slots=True)
class Endpoint:
    wire: Wire
    base_url: str
    model: str
    key: str | None = field(default=None, repr=False)


def auth_headers(wire: Wire, key: str | None) -> dict[str, str]:
    """How the provider is told who is asking: a bearer token, or Anthropic's own headers."""
    if wire == "messages":
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if key is not None:
            headers["x-api-key"] = key
        return headers
    return {} if key is None else {"Authorization": f"Bearer {key}"}


def complete(
    client: httpx.Client,
    endpoint: Endpoint,
    *,
    system: str,
    content: Content,
    schema: dict[str, Any] | None = None,
    name: str = "answer",
) -> str:
    """The model's answer as text. With a schema the answer is held to it (JSON); `name` is
    the schema's name on the chat-completions wire."""
    base = endpoint.base_url.rstrip("/")
    headers = auth_headers(endpoint.wire, endpoint.key)
    if endpoint.wire == "messages":
        response = client.post(f"{base}/messages", headers=headers,
                               json=_messages_body(endpoint.model, system, content, schema))
        response.raise_for_status()
        return _messages_text(response)
    body: dict[str, Any] = {
        "model": endpoint.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        }
    response = client.post(f"{base}/chat/completions", headers=headers, json=body)
    response.raise_for_status()
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise RuntimeError(MALFORMED) from error
    if not isinstance(text, str):
        raise RuntimeError(MALFORMED)
    return text


def _messages_body(
    model: str, system: str, content: Content, schema: dict[str, Any] | None
) -> dict[str, Any]:
    parts = content if isinstance(content, str) else _anthropic_parts(content)
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": parts}],
    }
    if schema is not None:
        body["output_config"] = {"format": {"type": "json_schema", "schema": portable(schema)}}
    return body


def _anthropic_parts(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The parts as Messages parts, within the API's many-image limits: refused here, with the
    limit named, rather than by a 400 that says nothing about which page."""
    images = sum(part.get("type") == "image_url" for part in content)
    if images > MAX_IMAGES:
        raise ValueError(f"a Messages request takes at most {MAX_IMAGES} images; "
                         f"this one has {images}")
    side = MANY_IMAGES_SIDE if images > MANY_IMAGES else MAX_IMAGE_SIDE
    parts = [_anthropic_part(part, side) for part in content]
    total = sum(len(part["source"]["data"]) for part in parts if part.get("type") == "image")
    if total > MAX_REQUEST_BASE64:
        raise ValueError(f"a Messages request takes at most {MAX_REQUEST_BASE64} base64 "
                         f"characters of images; this one has {total}")
    return parts


def _anthropic_part(part: dict[str, Any], side: int = MAX_IMAGE_SIDE) -> dict[str, Any]:
    """A chat-completions part as a Messages part: text is the same, an image is base64."""
    if part.get("type") != "image_url":
        return part
    header, _, data = part["image_url"]["url"].partition(",")
    if not header.startswith("data:") or not header.endswith(";base64"):
        raise ValueError("only data-URL images can be sent on the messages wire")
    media_type, data = _fitted(header.removeprefix("data:").removesuffix(";base64"), data, side)
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": data}}


def _fitted(media_type: str, data: str, side: int = MAX_IMAGE_SIDE) -> tuple[str, str]:
    """The image scaled down to what the Messages API takes; within its limits, untouched.
    The other wires scale on their side. The model answers in normalised coordinates, so a
    smaller picture of the same page changes no answer's meaning."""
    try:
        image = Image.open(BytesIO(base64.b64decode(data)))
        width, height = image.size
    except (OSError, ValueError):
        return media_type, data  # not an image this side can read; the provider judges it
    if max(width, height) <= side and len(data) <= MAX_IMAGE_BASE64:
        return media_type, data
    scale = min(1.0, side / max(width, height))
    while True:
        size = (max(1, int(width * scale)), max(1, int(height * scale)))
        resized = image.resize(size, Image.Resampling.LANCZOS)
        for kind, format_, page in (("image/png", "PNG", resized),
                                    ("image/jpeg", "JPEG", resized.convert("RGB"))):
            buffer = BytesIO()
            page.save(buffer, format=format_, **({"quality": 85} if format_ == "JPEG" else {}))
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            if len(encoded) <= MAX_IMAGE_BASE64:
                return kind, encoded
        scale *= 0.75


def _messages_text(response: httpx.Response) -> str:
    """The text blocks joined; any other block (a thinking block) is not the answer."""
    try:
        answer = response.json()
        stop = answer.get("stop_reason")
        text = "".join(block["text"] for block in answer["content"]
                       if block.get("type") == "text")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise RuntimeError(MALFORMED) from error
    if stop in _CUT_SHORT:
        raise RuntimeError(f"{MALFORMED} (stop_reason={stop})")
    if not text:
        raise RuntimeError(MALFORMED)
    return text
