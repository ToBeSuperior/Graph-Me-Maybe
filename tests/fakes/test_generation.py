from graphme.domain.enums import EvidenceKind
from graphme.domain.ids import canonical_json, sha256_bytes
from graphme.domain.models import EvidenceRecord, RetrievedContext, SourceLocator
from tests.fakes.generation import LexicalGenerationProvider


def test_lexical_summary_preserves_ordered_evidence_text() -> None:
    first = EvidenceRecord(
        id="evidence-1",
        document_id="document-1",
        node_id="node-1",
        kind=EvidenceKind.SOURCE_TEXT,
        locator=SourceLocator(file_name="report.docx", label="paragraph 1"),
        text="Revenue reached 100.",
    )
    second = EvidenceRecord(
        id="evidence-2",
        document_id="document-1",
        node_id="node-2",
        kind=EvidenceKind.SOURCE_TEXT,
        locator=SourceLocator(file_name="report.docx", label="paragraph 2"),
        text="Costs reached 40.",
    )

    summary = LexicalGenerationProvider().summarize_embedding_input(
        evidence=(first, second),
        reason="provider token limit",
    )

    assert summary == "Revenue reached 100.\nCosts reached 40."


def test_lexical_caption_links_ordered_evidence() -> None:
    first = EvidenceRecord(
        id="evidence-1",
        document_id="document-1",
        node_id="node-1",
        kind=EvidenceKind.SOURCE_TEXT,
        locator=SourceLocator(file_name="report.docx", label="paragraph 1"),
        text="Revenue reached 100.",
    )
    second = EvidenceRecord(
        id="evidence-2",
        document_id="document-1",
        node_id="node-1",
        kind=EvidenceKind.SOURCE_TEXT,
        locator=SourceLocator(file_name="report.docx", label="paragraph 2"),
        text="Costs reached 40.",
    )

    caption = LexicalGenerationProvider().caption(
        node_id="node-1",
        evidence=(first, second),
    )

    source_hash = sha256_bytes(
        canonical_json(
            [first.model_dump(mode="json"), second.model_dump(mode="json")]
        ).encode()
    )
    assert caption.document_id == "document-1"
    assert caption.node_id == "node-1"
    assert caption.summary == "Revenue reached 100.\nCosts reached 40."
    assert tuple(claim.evidence_ids for claim in caption.claims) == (
        ("evidence-1",),
        ("evidence-2",),
    )
    assert caption.model == "deterministic-lexical"
    assert caption.source_hash == source_hash


def test_lexical_answer_uses_ordered_source_evidence() -> None:
    first = EvidenceRecord(
        id="evidence-1",
        document_id="document-1",
        node_id="node-1",
        kind=EvidenceKind.SOURCE_TEXT,
        locator=SourceLocator(file_name="report.docx", label="paragraph 1"),
        text="Revenue reached 100.",
    )
    generated = EvidenceRecord(
        id="caption-1",
        document_id="document-1",
        node_id="node-1",
        kind=EvidenceKind.GENERATED_CAPTION,
        locator=SourceLocator(file_name="report.docx", label="paragraph 1"),
        text="Generated overview.",
        generated=True,
    )
    second = EvidenceRecord(
        id="evidence-2",
        document_id="document-1",
        node_id="node-2",
        kind=EvidenceKind.SOURCE_TEXT,
        locator=SourceLocator(file_name="report.docx", label="paragraph 2"),
        text="Costs reached 40.",
    )
    context = RetrievedContext(
        document_id="document-1",
        question="What happened?",
        hits=(),
        node_scores={"node-1": 0.9, "node-2": 0.8},
        selected_node_ids=("node-1", "node-2"),
        evidence_ids=("evidence-1", "caption-1", "evidence-2"),
        hops_used=0,
        estimated_tokens=12,
    )

    answer = LexicalGenerationProvider().answer(
        context=context,
        evidence=(first, generated, second),
    )

    assert answer.text == "Revenue reached 100.\nCosts reached 40."
    assert answer.citation_evidence_ids == ("evidence-1", "evidence-2")
