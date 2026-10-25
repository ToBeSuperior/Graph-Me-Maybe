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


@dataclass(frozen=True)
class ModelFile:
    """A model run on this machine: one GGUF file, where it is fetched from, what it must hash
    to, and its size."""

    url: str
    sha256: str
    size: int
    file: str


@dataclass(frozen=True, slots=True)
class Embedder:
    base_url: str
    key_env: str
    """The one environment variable its key is read from; empty for a model run here."""
    model: str
    dimensions: int
    query_prefix: str = ""
    """What a question is written after, as the model was trained to read one; passages go as
    they are."""
    model_file: ModelFile | None = None
    """For a model run on this machine (`adapters/local_models.py`): its file. Its address is
    the server started for it, not `base_url`."""


_HF = "https://huggingface.co"
LOCAL_EMBEDDERS: dict[str, Embedder] = {
    # Microsoft's harrier-oss-v1-0.6b (MIT), 94 languages; a question goes after an
    # instruction, as its card asks. Quantized to Q8_0.
    "general": Embedder(
        base_url="", key_env="", model="harrier-oss-v1-0.6b", dimensions=1024,
        query_prefix="Instruct: Given a search question, retrieve the document passage that "
                     "answers it\nQuery: ",
        model_file=ModelFile(
            url=f"{_HF}/mradermacher/harrier-oss-v1-0.6b-GGUF/resolve/"
                "d79decec1ab9442e969e79804515b9c31683d30e/harrier-oss-v1-0.6b.Q8_0.gguf",
            sha256="ba2c9408f82cfdb73aaf70aaea9f125f3fb24c5e76ce0efb469e1530e5ec53e4",
            size=639448320, file="harrier-oss-v1-0.6b.Q8_0.gguf")),
    # Korea University's KURE-v1 (MIT), bge-m3 trained further on Korean retrieval; no
    # instruction. Quantized to Q4_K_M.
    "korean": Embedder(
        base_url="", key_env="", model="KURE-v1", dimensions=1024,
        model_file=ModelFile(
            url=f"{_HF}/mykor/KURE-v1-gguf/resolve/"
                "65128e4341eee21ccd8bc86c033fbc44ff7b4871/KURE-v1-Q4_K_M.gguf",
            sha256="468444a5f74f6e39c77cc6c7c0d839b07b402dfcb94208da0895a2280a7056a3",
            size=437779008, file="KURE-v1-Q4_K_M.gguf")),
}
"""The search models the app runs on this machine, the same in either build: no key, nothing
sent off it. `general` is the default."""
DEFAULT_EMBEDDER = "general"
