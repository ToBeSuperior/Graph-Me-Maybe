from graphme.domain.ids import canonical_json, content_id, sha256_bytes
from graphme.domain.models import (
    Caption,
    EvidenceClaim,
    EvidenceRecord,
    GeneratedAnswer,
    RetrievedContext,
)


class LexicalGenerationProvider:
    @property
    def model(self) -> str:
        return "deterministic-lexical"

    def caption(
        self,
        *,
        node_id: str,
        evidence: tuple[EvidenceRecord, ...],
        correction: str | None = None,
    ) -> Caption:
        serialized = [item.model_dump(mode="json") for item in evidence]
        source_hash = sha256_bytes(canonical_json(serialized).encode())
        return Caption(
            id=content_id("caption", source_hash),
            document_id=evidence[0].document_id,
            node_id=node_id,
            summary="\n".join(item.text for item in evidence if item.text is not None),
            confidence=1.0,
            claims=tuple(
                EvidenceClaim(statement=item.text, evidence_ids=(item.id,))
                for item in evidence
                if item.text is not None
            ),
            model=self.model,
            source_hash=source_hash,
        )

    def summarize_embedding_input(
        self,
        *,
        evidence: tuple[EvidenceRecord, ...],
        reason: str,
    ) -> str:
        return "\n".join(item.text for item in evidence if item.text is not None)

    def answer(
        self,
        *,
        context: RetrievedContext,
        evidence: tuple[EvidenceRecord, ...],
        repair: str | None = None,
    ) -> GeneratedAnswer:
        source_evidence = tuple(
            item for item in evidence if not item.generated and item.text is not None
        )
        return GeneratedAnswer(
            text="\n".join(item.text for item in source_evidence),
            citation_evidence_ids=tuple(item.id for item in source_evidence),
        )
