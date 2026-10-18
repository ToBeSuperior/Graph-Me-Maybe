from typing import Protocol

from graphme.domain.models import (
    Caption,
    EvidenceRecord,
    GeneratedAnswer,
    RetrievedContext,
)


class GenerationProvider(Protocol):
    @property
    def model(self) -> str: ...

    def caption(
        self,
        *,
        node_id: str,
        evidence: tuple[EvidenceRecord, ...],
        correction: str | None = None,
    ) -> Caption: ...

    def summarize_embedding_input(
        self, *, evidence: tuple[EvidenceRecord, ...], reason: str
    ) -> str: ...

    def answer(
        self,
        *,
        context: RetrievedContext,
        evidence: tuple[EvidenceRecord, ...],
        repair: str | None = None,
    ) -> GeneratedAnswer: ...
