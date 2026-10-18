import json
from typing import Any

import httpx
from pydantic import ValidationError

from graphme.adapters.chat import MALFORMED, Endpoint, complete
from graphme.domain.ids import canonical_json, content_id, sha256_bytes
from graphme.domain.models import (
    Caption,
    EvidenceClaim,
    EvidenceRecord,
    GeneratedAnswer,
    RetrievedContext,
)
from graphme.providers import Wire

_EMBEDDING_INPUT_SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"summary": {"type": "string", "minLength": 1}},
    "required": ["summary"],
}

_ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "citation_evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "abstained": {"type": "boolean"},
        "abstention_reason": {"type": ["string", "null"]},
    },
    "required": [
        "text",
        "citation_evidence_ids",
        "abstained",
        "abstention_reason",
    ],
}

_CAPTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "entities": {"type": "array", "items": {"type": "string"}},
        "measures": {"type": "array", "items": {"type": "string"}},
        "trends": {"type": "array", "items": {"type": "string"}},
        "time_range": {"type": ["string", "null"]},
        "source_context": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "statement": {"type": "string", "minLength": 1},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
                "required": ["statement", "evidence_ids"],
            },
        },
    },
    "required": [
        "summary",
        "entities",
        "measures",
        "trends",
        "time_range",
        "source_context",
        "confidence",
        "claims",
    ],
}


class ChatCompletionsGenerationAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        client: httpx.Client,
        api_key: str | None = None,
        wire: Wire = "chat_completions",
    ) -> None:
        self._endpoint = Endpoint(wire, base_url, model, api_key)
        self._client = client

    @property
    def model(self) -> str:
        return self._endpoint.model

    def _ask(self, *, system: str, prompt: Any, name: str, schema: dict[str, Any]) -> Any:
        """The model's JSON answer to one prompt; a malformed one is a RuntimeError."""
        text = complete(self._client, self._endpoint, system=system,
                        content=canonical_json(prompt), schema=schema, name=name)
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError(MALFORMED) from error

    def summarize_embedding_input(
        self,
        *,
        evidence: tuple[EvidenceRecord, ...],
        reason: str,
    ) -> str:
        if not evidence:
            raise ValueError("embedding summary evidence must not be empty")
        prompt = {
            "evidence": [record.model_dump(mode="json") for record in evidence],
            "reason": reason,
        }
        generated = self._ask(
            system=(
                "Summarize the supplied evidence for embedding while preserving its "
                "factual content. Return only the requested JSON."
            ),
            prompt=prompt, name="embedding_input_summary",
            schema=_EMBEDDING_INPUT_SUMMARY_SCHEMA,
        )
        try:
            summary = generated["summary"]
            if not isinstance(summary, str) or not summary:
                raise TypeError("summary must be a nonempty string")
        except (KeyError, TypeError) as error:
            raise RuntimeError(MALFORMED) from error
        return summary

    def caption(
        self,
        *,
        node_id: str,
        evidence: tuple[EvidenceRecord, ...],
        correction: str | None = None,
    ) -> Caption:
        if not evidence:
            raise ValueError("caption evidence must not be empty")
        document_ids = {record.document_id for record in evidence}
        if len(document_ids) != 1:
            raise ValueError("caption evidence must belong to one document")

        serialized_evidence = [record.model_dump(mode="json") for record in evidence]
        source_hash = sha256_bytes(canonical_json(serialized_evidence).encode())
        prompt: dict[str, Any] = {
            "node_id": node_id,
            "evidence": serialized_evidence,
        }
        if correction is not None:
            prompt["correction"] = correction
        generated = self._ask(
            system=(
                "Create an evidence-linked document caption. "
                "Return only the requested JSON."
            ),
            prompt=prompt, name="document_caption", schema=_CAPTION_SCHEMA,
        )
        try:
            claims = tuple(
                EvidenceClaim.model_validate(value) for value in generated["claims"]
            )
            return Caption(
                id=content_id("caption", source_hash),
                document_id=next(iter(document_ids)),
                node_id=node_id,
                summary=generated["summary"],
                entities=tuple(generated["entities"]),
                measures=tuple(generated["measures"]),
                trends=tuple(generated["trends"]),
                time_range=generated["time_range"],
                source_context=generated["source_context"],
                confidence=generated["confidence"],
                claims=claims,
                model=self._endpoint.model,
                source_hash=source_hash,
            )
        except (KeyError, TypeError, ValidationError, ValueError) as error:
            raise RuntimeError(MALFORMED) from error

    def answer(
        self,
        *,
        context: RetrievedContext,
        evidence: tuple[EvidenceRecord, ...],
        repair: str | None = None,
    ) -> GeneratedAnswer:
        prompt: dict[str, Any] = {
            "context": context.model_dump(mode="json"),
            "evidence": [record.model_dump(mode="json") for record in evidence],
        }
        if repair is not None:
            prompt["repair"] = repair
        generated = self._ask(
            system=(
                "Answer the question using only the supplied evidence. "
                "A factual answer must cite at least one non-generated "
                "evidence ID. If the evidence is insufficient, explicitly "
                "abstain with no citations and a reason. Return only the "
                "requested JSON."
            ),
            prompt=prompt, name="document_answer", schema=_ANSWER_SCHEMA,
        )
        try:
            return GeneratedAnswer.model_validate(generated)
        except (TypeError, ValidationError, ValueError) as error:
            raise RuntimeError(MALFORMED) from error
