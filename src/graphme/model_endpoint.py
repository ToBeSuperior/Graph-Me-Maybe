"""Where the chat model and the embedder are reached, and with what key.

GRAPHME_PROVIDER picks who answers every chat call -- OpenAI, Anthropic (its own Messages
API) or Gemini (its OpenAI-compatible layer) -- and its key is read from that provider's own
variable and nowhere else. Embeddings come from OpenAI or Gemini; Anthropic has none, so with
Anthropic searching needs an OpenAI key, or GRAPHME_EMBEDDING_PROVIDER=gemini with a Gemini
key. GRAPHME_MODEL_BASE_URL can name another server speaking the same wire: over HTTPS
anywhere, or over plain HTTP only on this machine, so a key never crosses a network in the
clear.
"""
from __future__ import annotations

from urllib.parse import urlparse

from graphme.providers import Embedder, Provider

DEFAULT_PROVIDER = "openai"
_GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai"
PROVIDERS = {
    "openai": Provider(wire="chat_completions", base_url="https://api.openai.com/v1",
                       key_env="OPENAI_API_KEY", model="gpt-5.6-luna"),
    # Haiku: the outline's page readings and page-break joins were measured and tuned on it.
    "anthropic": Provider(wire="messages", base_url="https://api.anthropic.com/v1",
                          key_env="ANTHROPIC_API_KEY", model="claude-haiku-5-5"),
    "gemini": Provider(wire="chat_completions", base_url=_GEMINI,
                       key_env="GEMINI_API_KEY", model="gemini-2.5-flash"),
}
EMBEDDERS = {
    "openai": Embedder(base_url="https://api.openai.com/v1", key_env="OPENAI_API_KEY",
                       model="text-embedding-3-large", dimensions=3072),
    "gemini": Embedder(base_url=_GEMINI, key_env="GEMINI_API_KEY",
                       model="gemini-embedding-001", dimensions=3072),
}
KEY_REQUIRED = True
"""A hosted API refuses every call without one, so a missing key is said at startup."""

_THIS_MACHINE = {"127.0.0.1", "localhost", "::1"}


def check_base_url(value: str) -> str:
    """An HTTPS address, or an HTTP one on this machine."""
    parsed = urlparse(value)
    if not parsed.hostname or parsed.scheme not in {"http", "https"}:
        raise ValueError("GRAPHME_MODEL_BASE_URL must be an HTTP(S) URL")
    if parsed.scheme == "http" and parsed.hostname not in _THIS_MACHINE:
        raise ValueError("GRAPHME_MODEL_BASE_URL must use HTTPS unless it is on this machine")
    return value.rstrip("/")
