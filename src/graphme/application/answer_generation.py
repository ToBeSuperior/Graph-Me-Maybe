from graphme.domain.models import Answer, Citation, RetrievedContext
from graphme.ports.generation import GenerationProvider
from graphme.ports.storage import GraphStore


class AnswerGenerator:
    def __init__(self, *, store: GraphStore, provider: GenerationProvider) -> None:
        self._store = store
        self._provider = provider

    def generate(self, *, context: RetrievedContext) -> Answer:
        loaded = self._store.get_evidence(context.evidence_ids)
        evidence_by_id = {record.id: record for record in loaded}
        missing_ids = tuple(
            evidence_id
            for evidence_id in context.evidence_ids
            if evidence_id not in evidence_by_id
        )
        if missing_ids:
            raise ValueError("context evidence is missing: " + ", ".join(missing_ids))
        evidence = tuple(evidence_by_id[evidence_id] for evidence_id in context.evidence_ids)
        if any(record.document_id != context.document_id for record in evidence):
            raise ValueError("context evidence belongs to another document")
        source_evidence_by_id = {
            record.id: record for record in evidence if not record.generated
        }
        if not source_evidence_by_id:
            raise ValueError("answer requires source evidence")

        generated = self._provider.answer(context=context, evidence=evidence)
        repaired = False
        if not generated.abstained:
            invalid_ids = tuple(
                evidence_id
                for evidence_id in generated.citation_evidence_ids
                if evidence_id not in source_evidence_by_id
            )
            if invalid_ids or not generated.citation_evidence_ids:
                generated = self._provider.answer(
                    context=context,
                    evidence=evidence,
                    repair=(
                        "Provide at least one citation using only supplied source evidence "
                        "IDs, or explicitly abstain: "
                        + ", ".join(source_evidence_by_id)
                    ),
                )
                repaired = True

        if generated.abstained:
            citations: tuple[Citation, ...] = ()
        else:
            if not generated.citation_evidence_ids:
                raise ValueError("non-abstaining answer requires source citations")
            try:
                citations = tuple(
                    Citation(
                        evidence_id=source_evidence_by_id[evidence_id].id,
                        node_id=source_evidence_by_id[evidence_id].node_id,
                        locator=source_evidence_by_id[evidence_id].locator,
                    )
                    for evidence_id in generated.citation_evidence_ids
                )
            except KeyError as error:
                raise ValueError("answer cites unknown or generated evidence") from error

        answer = Answer(
            document_id=context.document_id,
            question=context.question,
            text=generated.text,
            citations=citations,
            model=self._provider.model,
            repaired=repaired,
            abstained=generated.abstained,
            abstention_reason=generated.abstention_reason,
        )
        self._store.save_answer(answer)
        return answer
