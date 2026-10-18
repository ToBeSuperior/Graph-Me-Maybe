from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from graphme.application.ingest import IngestResult
from graphme.application.inspect import InspectionReport
from graphme.config import Settings
from graphme.domain.enums import DocumentFormat, IngestionStatus
from graphme.domain.models import Answer, Citation, IngestionRun, SourceLocator


class RecordingQueryService:
    def __init__(self, answer: Answer) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def query(self, *, document_id: str, question: str) -> Answer:
        self.calls.append((document_id, question))
        return self.answer


class RecordingIngestService:
    def __init__(self, result: IngestResult | None = None) -> None:
        self.result = result
        self.calls: list[Path] = []

    def ingest(self, source: Path) -> IngestResult:
        self.calls.append(source)
        if self.result is None:
            raise AssertionError("ingestion was not expected")
        return self.result


class RecordingInspectService:
    def __init__(self, report: str = "unused", record=None) -> None:
        self.report = report
        self.record = record
        self.calls: list[str] = []

    def inspect(self, document_id: str) -> str:
        self.calls.append(document_id)
        return self.report

    def inspect_record(self, document_id: str):
        self.calls.append(document_id)
        if self.record is None:
            raise AssertionError("structured inspection was not expected")
        return self.record


class RecordingEvaluationService:
    def __init__(self, report: str = "unused") -> None:
        self.report = report
        self.calls: list[Path] = []

    def evaluate(self, suite: Path) -> str:
        self.calls.append(suite)
        return self.report


def test_exported_app_exposes_all_commands_without_provider_calls() -> None:
    from graphme.cli import app

    invocation = CliRunner().invoke(app, ["--help"])

    assert invocation.exit_code == 0
    for command in ("ingest", "query", "inspect", "evaluate", "xlsx-bundle", "export-neo4j"):
        assert command in invocation.stdout


def test_repeated_query_commands_do_not_invoke_ingestion() -> None:
    try:
        from graphme.cli import create_app
    except ImportError:
        pytest.fail("query CLI is unavailable", pytrace=False)

    answer = Answer(
        document_id="doc-1",
        question="What was revenue?",
        text="Revenue was 100.",
        citations=(
            Citation(
                evidence_id="ev-1",
                node_id="node-1",
                locator=SourceLocator(
                    file_name="report.xlsx",
                    label="A1:B2",
                    sheet="Sheet1",
                    cell_range="A1:B2",
                ),
            ),
        ),
        model="gpt",
    )
    query_service = RecordingQueryService(answer)
    ingest_service = RecordingIngestService()
    runner = CliRunner()
    app = create_app(
        query_service=query_service,
        ingest_service=ingest_service,
        inspect_service=RecordingInspectService(),
        evaluation_service=RecordingEvaluationService(),
    )

    first = runner.invoke(app, ["query", "doc-1", "What was revenue?"])
    second = runner.invoke(app, ["query", "doc-1", "What was revenue?"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.stdout == second.stdout
    assert first.stdout == (
        "Revenue was 100.\n"
        "[report.xlsx > Sheet1!A1:B2 | node:node-1]\n"
    )
    assert query_service.calls == [
        ("doc-1", "What was revenue?"),
        ("doc-1", "What was revenue?"),
    ]
    assert ingest_service.calls == []


def test_ingest_command_prints_document_id_and_reuse_status(tmp_path: Path) -> None:
    try:
        from graphme.cli import create_app
    except ImportError:
        pytest.fail("ingest CLI is unavailable", pytrace=False)

    source = tmp_path / "report.xlsx"
    source.touch()
    result = IngestResult(
        document_id="doc-1",
        content_hash="a" * 64,
        reused=True,
        run=IngestionRun(
            id="run-1",
            document_id="doc-1",
            source_path=source,
            status=IngestionStatus.SUCCEEDED,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        ),
    )
    ingest_service = RecordingIngestService(result)
    query_service = RecordingQueryService(
        Answer(
            document_id="doc-1",
            question="unused",
            text="unused",
            citations=(),
            model="gpt",
        )
    )
    runner = CliRunner()
    app = create_app(
        query_service=query_service,
        ingest_service=ingest_service,
        inspect_service=RecordingInspectService(),
        evaluation_service=RecordingEvaluationService(),
    )

    invocation = runner.invoke(app, ["ingest", str(source)])

    assert invocation.exit_code == 0
    assert invocation.stdout == "document_id=doc-1\nreused=true\n"
    assert ingest_service.calls == [source]
    assert query_service.calls == []


def test_shared_runtime_wires_live_providers_and_complete_index_pipeline(
    tmp_path: Path,
) -> None:
    from graphme.adapters.embedding.text import TextEmbeddingProvider
    from graphme.adapters.generation.chat_completions import ChatCompletionsGenerationAdapter
    from graphme.adapters.rendering.page_image import PageImageRenderer
    from graphme.adapters.vision.chat_completions import ChatCompletionsVLMAdapter
    from graphme.application.embedding import EmbeddingGenerator
    from graphme.application.grounding import GroundedDocumentBuilder
    from graphme.application.index_representation import MultimodalIndexComposer
    from graphme.runtime import create_runtime

    settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-proxy-key",
        embedding_key="test-embed-key",
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _request: None)) as client:
        runtime = create_runtime(settings=settings, client=client)

    index_builder = runtime.ingest._index_builder
    assert isinstance(index_builder._grounding, GroundedDocumentBuilder)
    assert isinstance(index_builder._grounding._renderer, PageImageRenderer)
    assert isinstance(index_builder._grounding._vision_provider, ChatCompletionsVLMAdapter)
    assert index_builder._relations._provider is index_builder._grounding._vision_provider
    assert isinstance(index_builder._composer, MultimodalIndexComposer)
    assert isinstance(index_builder._embeddings, EmbeddingGenerator)
    assert isinstance(index_builder._embeddings._embedding_provider, TextEmbeddingProvider)
    assert isinstance(
        index_builder._embeddings._generation_provider,
        ChatCompletionsGenerationAdapter,
    )
    assert index_builder._embeddings._store is runtime.store


def test_shared_runtime_requires_the_embedding_key(tmp_path: Path) -> None:
    from graphme.runtime import create_runtime

    settings = Settings(data_dir=tmp_path / "data", api_key="test-proxy-key")
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _request: None)) as client,
        pytest.raises(
            ValueError,
            match="is required to embed or search",
        ),
    ):
        create_runtime(settings=settings, client=client)


def test_shared_runtime_selects_deterministic_offline_providers(tmp_path: Path) -> None:
    from graphme.adapters.offline import (
        DeterministicGenerationProvider,
        DeterministicVisionProvider,
        HashEmbeddingProvider,
    )
    from graphme.runtime import create_runtime

    runtime = create_runtime(
        settings=Settings(
            profile="test",
            embedding_provider="hash",
            data_dir=tmp_path / "data",
        )
    )
    index_builder = runtime.ingest._index_builder

    assert isinstance(index_builder._grounding._vision_provider, DeterministicVisionProvider)
    assert isinstance(index_builder._embeddings._embedding_provider, HashEmbeddingProvider)
    assert isinstance(
        index_builder._embeddings._generation_provider,
        DeterministicGenerationProvider,
    )


def test_inspect_command_prints_document_report() -> None:
    try:
        from graphme.cli import create_app
    except ImportError:
        pytest.fail("inspect CLI is unavailable", pytrace=False)

    inspect_service = RecordingInspectService(
        "document_id=doc-1\nnodes=3\nedges=2\nevidence=3"
    )
    runner = CliRunner()
    app = create_app(
        query_service=RecordingQueryService(
            Answer(
                document_id="doc-1",
                question="unused",
                text="unused",
                citations=(),
                model="gpt",
            )
        ),
        ingest_service=RecordingIngestService(),
        inspect_service=inspect_service,
        evaluation_service=RecordingEvaluationService(),
    )

    invocation = runner.invoke(app, ["inspect", "doc-1"])

    assert invocation.exit_code == 0
    assert invocation.stdout == (
        "document_id=doc-1\nnodes=3\nedges=2\nevidence=3\n"
    )
    assert inspect_service.calls == ["doc-1"]


def test_inspect_command_prints_structured_json() -> None:
    try:
        from graphme.cli import create_app
    except ImportError:
        pytest.fail("inspect CLI is unavailable", pytrace=False)

    report = InspectionReport(
        document_id="doc-1",
        source_path=Path("report.xlsx"),
        file_name="report.xlsx",
        format=DocumentFormat.XLSX,
        content_hash="a" * 64,
        ready=False,
        latest_ingestion_run=None,
        warnings=(),
        node_count=1,
        edge_count=0,
        evidence_count=1,
        source_evidence_count=1,
        generated_evidence_count=0,
        subgraph_count=2,
        embedding_count=1,
        missing_embedding_count=1,
        embedding_provider="hash",
        embedding_model="hash-v1",
        embedding_dimensions=8,
        embedding_coverage=0.5,
    )
    inspect_service = RecordingInspectService(record=report)
    app = create_app(
        query_service=RecordingQueryService(
            Answer(
                document_id="doc-1",
                question="unused",
                text="unused",
                citations=(),
                model="gpt",
            )
        ),
        ingest_service=RecordingIngestService(),
        inspect_service=inspect_service,
        evaluation_service=RecordingEvaluationService(),
    )

    invocation = CliRunner().invoke(app, ["inspect", "doc-1", "--json"])

    assert invocation.exit_code == 0
    assert '"document_id":"doc-1"' in invocation.stdout
    assert '"embedding_coverage":0.5' in invocation.stdout
    assert inspect_service.calls == ["doc-1"]


def test_evaluate_command_prints_suite_report(tmp_path: Path) -> None:
    try:
        from graphme.cli import create_app
    except ImportError:
        pytest.fail("evaluate CLI is unavailable", pytrace=False)

    suite = tmp_path / "suite.json"
    suite.touch()
    evaluation_service = RecordingEvaluationService(
        "cases=2\nevidence_recall=1.000"
    )
    runner = CliRunner()
    app = create_app(
        query_service=RecordingQueryService(
            Answer(
                document_id="doc-1",
                question="unused",
                text="unused",
                citations=(),
                model="gpt",
            )
        ),
        ingest_service=RecordingIngestService(),
        inspect_service=RecordingInspectService(),
        evaluation_service=evaluation_service,
    )

    invocation = runner.invoke(app, ["evaluate", str(suite)])

    assert invocation.exit_code == 0
    assert invocation.stdout == "cases=2\nevidence_recall=1.000\n"
    assert evaluation_service.calls == [suite]


def test_shared_runtime_gives_the_page_renderer_a_browser(tmp_path: Path) -> None:
    # Without one every OOXML format fails closed, so the runtime must supply it.
    from graphme import model_endpoint
    from graphme.adapters.rendering.browser_office import BrowserOfficeRenderer
    from graphme.config import load_settings
    from graphme.runtime import create_runtime

    settings = load_settings(
        environ={
            "GRAPHME_DATA_DIR": str(tmp_path),
            "GRAPHME_VIEWER_DIR": str(tmp_path / "viewer"),
            model_endpoint.PROVIDERS[model_endpoint.DEFAULT_PROVIDER].key_env: "test-key",
            model_endpoint.EMBEDDERS["openai"].key_env: "test-key",
        }
    )
    with httpx.Client() as client:
        runtime = create_runtime(client=client, settings=settings)
    assert isinstance(runtime.page_renderer._browser_renderer, BrowserOfficeRenderer)


def test_export_neo4j_writes_cypher_and_json(tmp_path: Path) -> None:
    from graphme.cli import create_app
    from tests.fakes.documents import named_document

    class _Graphs(RecordingInspectService):
        def graph(self, document_id: str):
            self.calls.append(document_id)
            return named_document("d", "a.pdf", names=("t",))

    app = create_app(
        query_service=RecordingQueryService(None),
        ingest_service=RecordingIngestService(),
        inspect_service=_Graphs(),
        evaluation_service=RecordingEvaluationService(),
    )
    cypher, records = tmp_path / "g.cypher", tmp_path / "g.json"
    assert CliRunner().invoke(app, ["export-neo4j", "doc", str(cypher)]).exit_code == 0
    assert CliRunner().invoke(app, ["export-neo4j", "doc", str(records), "--json"]).exit_code == 0
    assert "MERGE (n:DocNode" in cypher.read_text(encoding="utf-8")
    written = json.loads(records.read_text(encoding="utf-8"))["nodes"]
    assert {n["id"] for n in written} == {"d", "d-ext0"}


def test_export_corpus_writes_what_the_graph_space_holds(tmp_path: Path) -> None:
    from graphme.cli import create_app

    dumped: list[Path] = []
    chosen: list = []

    class _Corpus:
        def dump(self, bundle: Path, target: Path) -> None:
            dumped.append(bundle / "import.sh")
            target.write_bytes(b"dump")

        def bundle(self, folder: Path, document_ids=None) -> int:
            from graphme.adapters.export.neo4j_import import write_import_bundle
            from tests.fakes.corpus import rows_of

            write_import_bundle(rows_of(self.records(document_ids)), folder)
            return 2

        def records(self, document_ids=None):
            chosen.append(document_ids)
            return {
                "nodes": [
                    {"id": "a", "labels": ["DocNode", "Document"], "properties": {"id": "a"}},
                    {"id": "b", "labels": ["DocNode", "Document"], "properties": {"id": "b"}},
                    {"id": "m", "labels": ["DocNode", "ExternalDocument"],
                     "properties": {"id": "m"}},
                ],
                "relationships": [
                    {"id": "same-as:m->b", "type": "SAME_AS", "start": "m", "end": "b",
                     "properties": {}},
                ],
            }

    app = create_app(
        query_service=RecordingQueryService(None),
        ingest_service=RecordingIngestService(),
        inspect_service=RecordingInspectService(),
        evaluation_service=RecordingEvaluationService(),
        corpus_service=_Corpus(),
    )
    bundle, cypher, records = tmp_path / "bundle", tmp_path / "all.cypher", tmp_path / "all.json"
    # By default the space leaves as Neo4j's own bulk import reads it.
    result = CliRunner().invoke(app, ["export-corpus", str(bundle)])
    assert result.exit_code == 0 and "documents=2" in result.output
    assert (bundle / "import.sh").is_file() and (bundle / "relationships_SAME_AS.csv").is_file()
    assert CliRunner().invoke(app, ["export-corpus", str(cypher), "--cypher"]).exit_code == 0
    # A .dump is the bundle loaded and dumped by neo4j-admin: what Aura and Desktop take.
    dump = tmp_path / "graph.dump"
    assert CliRunner().invoke(app, ["export-corpus", str(dump), "--dump"]).exit_code == 0
    assert dump.read_bytes() == b"dump" and dumped[0].name == "import.sh"
    some = tmp_path / "some"
    assert CliRunner().invoke(
        app, ["export-corpus", str(some), "--document", "a", "--document", "b"]
    ).exit_code == 0
    assert chosen[-1] == ["a", "b"] and chosen[0] is None
    assert CliRunner().invoke(app, ["export-corpus", str(records), "--json"]).exit_code == 0
    assert ":SAME_AS" in cypher.read_text(encoding="utf-8")
    links = json.loads(records.read_text(encoding="utf-8"))["relationships"]
    assert [(r["start"], r["end"]) for r in links if r["type"] == "SAME_AS"] == [("m", "b")]

def test_ingesting_a_folder_ingests_every_document_in_it_and_goes_on_past_a_failure(
    tmp_path: Path,
) -> None:
    from graphme.cli import create_app

    for name in ("a.pdf", "broken.docx", "sub/c.xlsx", "notes.txt"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    class _Ingest(RecordingIngestService):
        def ingest(self, source: Path) -> IngestResult:
            self.calls.append(source)
            if source.name == "broken.docx":
                raise ValueError("cannot be read")
            return SimpleNamespace(document_id=f"id-{source.stem}", reused=False)  # type: ignore[return-value]

    ingest = _Ingest()
    app = create_app(
        query_service=RecordingQueryService(None),
        ingest_service=ingest,
        inspect_service=RecordingInspectService(),
        evaluation_service=RecordingEvaluationService(),
    )

    result = CliRunner().invoke(app, ["ingest", str(tmp_path)])

    assert [path.name for path in ingest.calls] == ["a.pdf", "broken.docx", "c.xlsx"]
    assert "document_id=id-a" in result.output and "document_id=id-c" in result.output
    assert "failed=" in result.output and "broken.docx" in result.output
    assert "ingested=2 failed=1" in result.output
    assert result.exit_code == 1  # a folder with a document left out did not all go in


def test_build_graph_reads_a_folder_into_one_graph_and_says_each_step(tmp_path: Path) -> None:
    from graphme.application.corpus_build import BuildReport
    from graphme.cli import create_app

    asked: list[Path] = []

    class _Corpus:
        def build(self, folder: Path, on_step, again=False) -> BuildReport:
            asked.append(folder)
            on_step("read", 2, 2)
            on_step("put", 2, 2)
            on_step("link", 1, 1)
            return BuildReport(processed=2, failed=((tmp_path / "x.docx", "broken"),), links=1)

    app = create_app(
        query_service=RecordingQueryService(None),
        ingest_service=RecordingIngestService(),
        inspect_service=RecordingInspectService(),
        evaluation_service=RecordingEvaluationService(),
        corpus_service=_Corpus(),
    )

    result = CliRunner().invoke(app, ["build-graph", str(tmp_path)])

    assert asked == [tmp_path]
    assert result.output.splitlines()[:3] == ["read 2/2", "put 2/2", "link 1/1"]
    assert "processed=2 known=0 failed=1 links_judged=1" in result.output
    assert "failed=" in result.output and "x.docx" in result.output
    assert result.exit_code == 1


def test_the_graph_is_embedded_and_asked_from_the_command_line() -> None:
    from graphme.adapters.storage.graph_rag import GraphAnswer, GraphSource
    from graphme.cli import create_app

    asked: list[tuple[str, int]] = []

    class _Rag:
        def embed(self, on_progress) -> int:
            on_progress(3, 3)
            return 3

        def ask(self, question: str, *, top_k: int | None) -> GraphAnswer:
            asked.append((question, top_k))
            return GraphAnswer(
                text="압력 강하가 기준값 이하이면 합격 [spec.docx] [note.emb1a2b3c4d.pdf]",
                sources=[GraphSource(id="n1", score=0.91, document_id="d", file="spec.docx",
                                     members=["u1"], context=""),
                         GraphSource(id="n2", score=0.5, document_id="e",
                                     file="note.emb1a2b3c4d.pdf", members=["u2"], context="")],
            )

    app = create_app(
        query_service=RecordingQueryService(None),
        ingest_service=RecordingIngestService(),
        inspect_service=RecordingInspectService(),
        evaluation_service=RecordingEvaluationService(),
        rag_service=_Rag(),
    )

    embedded = CliRunner().invoke(app, ["embed-graph"])
    answered = CliRunner().invoke(app, ["ask", "판정 기준은?", "--top-k", "4"])

    assert embedded.exit_code == 0 and "embedded=3" in embedded.output
    assert answered.exit_code == 0
    assert asked == [("판정 기준은?", 4)]
    assert "압력 강하가 기준값 이하이면 합격" in answered.output
    assert "spec.docx" in answered.output and "n1" in answered.output
    # an attachment's digest tells two files apart for the program, not the reader
    assert "[note.pdf]" in answered.output and "emb1a2b3c4d" not in answered.output


def test_delete_document_names_what_goes_and_asks_first(tmp_path: Path) -> None:
    from graphme.cli import create_app

    deleted: list[list[str]] = []

    class _Corpus:
        def files(self, document_ids):
            return {i: f"{i}.docx" for i in document_ids if i in {"a", "b"}}

        def delete(self, document_ids):
            deleted.append(list(document_ids))

    app = create_app(
        query_service=RecordingQueryService(None), ingest_service=RecordingIngestService(),
        inspect_service=RecordingInspectService(), evaluation_service=RecordingEvaluationService(),
        corpus_service=_Corpus(),
    )

    refused = CliRunner().invoke(app, ["delete-document", "a"], input="n\n")
    assert "a.docx" in refused.output and deleted == []
    unknown = CliRunner().invoke(app, ["delete-document", "a", "zzz"], input="y\n")
    assert unknown.exit_code != 0 and "zzz" in unknown.output and deleted == []
    done = CliRunner().invoke(app, ["delete-document", "a", "b", "--yes"])
    assert done.exit_code == 0 and deleted == [["a", "b"]] and "deleted=2" in done.output


def test_reset_graph_says_how_much_goes_and_asks_first() -> None:
    from graphme.cli import create_app

    reset: list[bool] = []

    class _Corpus:
        def count(self) -> int:
            return 3

        def reset(self) -> int:
            reset.append(True)
            return 3

    app = create_app(
        query_service=RecordingQueryService(None), ingest_service=RecordingIngestService(),
        inspect_service=RecordingInspectService(), evaluation_service=RecordingEvaluationService(),
        corpus_service=_Corpus(),
    )

    refused = CliRunner().invoke(app, ["reset-graph"], input="n\n")
    assert "3" in refused.output and reset == []
    done = CliRunner().invoke(app, ["reset-graph", "--yes"])
    assert done.exit_code == 0 and reset == [True] and "deleted=3" in done.output


def test_xlsx_bundle_renders_with_the_configured_viewer_and_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openpyxl import Workbook

    from graphme.adapters.rendering.browser_office import BrowserOfficeRenderer
    from graphme.adapters.rendering.page_image import PageImageRenderer
    from graphme.cli import app

    built: dict[str, object] = {}

    def recording_init(self: object, **kwargs: object) -> None:
        built.update(kwargs)

    def stop(self: PageImageRenderer, *args: object) -> None:
        built["renderer"] = self._browser_renderer
        raise RuntimeError("stop here")

    monkeypatch.setattr(BrowserOfficeRenderer, "__init__", recording_init)
    monkeypatch.setattr(PageImageRenderer, "render_with_optional_mapping", stop)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GRAPHME_VIEWER_DIR", str(tmp_path / "viewer"))
    monkeypatch.setenv("GRAPHME_CHROME_EXECUTABLE", "/opt/invented/chrome")
    book = Workbook()
    book.active["A1"] = "invented"
    book.save(tmp_path / "invented.xlsx")

    result = CliRunner().invoke(app, ["xlsx-bundle", "invented.xlsx", "out"])

    assert "stop here" in repr(result.exception)
    assert built["renderer"] is not None
    assert built["viewer_directory"] == tmp_path / "viewer"
    assert built["chrome_executable"] == "/opt/invented/chrome"
