"""What a chat provider and an embedder are, as rows of `model_endpoint`'s tables.

The tables differ between the development and the public build (`model_endpoint.py` is the
one file the public tree replaces); the shape of a row does not, so it lives here, once.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Wire = Literal["chat_completions", "messages"]
"""How a request is written: OpenAI's chat completions (OpenAI, Gemini's compatible layer,
any server speaking it) or Anthropic's Messages API."""


@dataclass(frozen=True, slots=True)
class Provider:
    wire: Wire
    base_url: str
    key_env: str
    """The one environment variable its key is read from."""
    model: str
    """Used when GRAPHME_MODEL is not set."""


@dataclass(frozen=True, slots=True)
class Embedder:
    base_url: str
    key_env: str
    model: str
    dimensions: int
    tokens_per_input: int
    tokens_per_request: int
    inputs_per_request: int
