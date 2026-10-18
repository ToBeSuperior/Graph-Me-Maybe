from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Protocol

import httpx
import typer

from graphme.adapters.export.neo4j import graph_records, records_cypher, to_cypher
from graphme.adapters.storage.graph_rag import GraphAnswer
from graphme.api import MODEL_WAIT, GraphMe
from graphme.application.corpus_build import (
    BuildReport,
    BuildStep,
    StepListener,
)
from graphme.application.ingest import IngestResult, documents_in
from graphme.application.inspect import InspectionReport, InspectionService
from graphme.application.xlsx_bundle import XlsxBundleOrchestrator
from graphme.config import load_settings
from graphme.domain.models import Answer, ExtractedDocument
from graphme.runtime import (
    create_browser_renderer,
    create_neo4j_admin,
    create_runtime,
    create_store,
    evaluation_profile,
    settings_for_evaluation,
)


class QueryService(Protocol):
    def query(self, *, document_id: str, question: str) -> Answer: ...


class IngestService(Protocol):
    def ingest(self, source: Path) -> IngestResult: ...


class InspectService(Protocol):
    def inspect(self, document_id: str) -> str: ...

    def inspect_record(self, document_id: str) -> InspectionReport: ...

    def graph(self, document_id: str) -> ExtractedDocument: ...


class EvaluationService(Protocol):
    def evaluate(self, suite: Path) -> str: ...


class CorpusService(Protocol):
    """The graph space: a folder built into it, and what it holds."""

    def build(self, folder: Path, on_step: StepListener, again: bool = False) -> BuildReport: ...

    def records(
        self, document_ids: list[str] | None = None
    ) -> dict[str, list[dict[str, Any]]]: ...

    def bundle(self, folder: Path, document_ids: list[str] | None = None) -> int:
        """The import bundle written into `folder`, read from the space as it is written;
        how many documents left."""
        ...

    def dump(self, bundle: Path, target: Path) -> None:
        """The import bundle in `bundle` as one `.dump` file, written by `neo4j-admin`."""
        ...

    def files(self, document_ids: list[str]) -> dict[str, str]:
        """The file of each of these documents the space holds."""
        ...

    def count(self) -> int:
        """How many documents the space holds."""
        ...

    def reset(self) -> int:
        """Every document gone from the space; how many."""
        ...

    def delete(self, document_ids: list[str]) -> None:
        """The documents gone from the space, with all of theirs."""
        ...


class RagService(Protocol):
    """Questions over the graph space: its units embedded, and a question answered from them."""

    def embed(self, on_progress: Callable[[int, int], None]) -> int: ...

    def ask(self, question: str, *, top_k: int | None) -> GraphAnswer:
        """`top_k` None: as many passages as the settings say."""
        ...


def create_app(
    *,
    query_service: QueryService,
    ingest_service: IngestService,
    inspect_service: InspectService,
    evaluation_service: EvaluationService,
    corpus_service: CorpusService | None = None,
    rag_service: RagService | None = None,
) -> typer.Typer:
    app = typer.Typer()

    @app.callback()
    def main() -> None:
        pass

    @app.command(short_help="Documents into the single-document index (SQLite).")
    def ingest(source: Path) -> None:
        """One document, or every document in a folder -- a corpus is a folder of them."""
        if not source.is_dir():
            result = ingest_service.ingest(source)
            typer.echo(f"document_id={result.document_id}")
            typer.echo(f"reused={str(result.reused).lower()}")
            return
        failed = 0
        documents = documents_in(source)
        for path in documents:
            # One document that cannot be read is one document, not the folder -- whatever
            # stopped it, a provider that turned it away included.
            try:
                result = ingest_service.ingest(path)
            except Exception as error:  # noqa: BLE001 -- reported below, never swallowed
                failed += 1
                typer.echo(f"failed={path} error={error}")
                continue
            typer.echo(
                f"document_id={result.document_id} reused={str(result.reused).lower()}"
                f" source={path}"
            )
        typer.echo(f"ingested={len(documents) - failed} failed={failed}")
        if failed:
            raise typer.Exit(1)

    @app.command(short_help="Answer a question about one ingested document.")
    def query(document_id: str, question: str) -> None:
        answer = query_service.query(document_id=document_id, question=question)
        typer.echo(answer.text)
        for citation in answer.citations:
            typer.echo(citation.render())

    @app.command(short_help="Show one ingested document: its nodes, edges and evidence.")
    def inspect(
        document_id: str,
        json_output: bool = typer.Option(False, "--json"),
    ) -> None:
        if json_output:
            typer.echo(inspect_service.inspect_record(document_id).model_dump_json())
        else:
            typer.echo(inspect_service.inspect(document_id))

    @app.command("export-neo4j", short_help="One ingested document as Neo4j Cypher or records.")
    def export_neo4j(
        document_id: str,
        output: Path,
        json_output: bool = typer.Option(False, "--json", help="Neo4j records, not Cypher"),
    ) -> None:
        extracted = inspect_service.graph(document_id)
        output.write_text(
            json.dumps(graph_records(extracted), ensure_ascii=False, indent=2)
            if json_output
            else to_cypher(extracted),
            encoding="utf-8",
        )
        typer.echo(f"written={output}")

    @app.command("export-corpus",
                 short_help="The graph space for Neo4j: bundle, .dump, JSON or Cypher.")
    def export_corpus(
        output: Path,
        json_output: bool = typer.Option(False, "--json", help="Neo4j records in one file"),
        cypher_output: bool = typer.Option(
            False, "--cypher", help="a Cypher script of the graph alone, without passages"
        ),
        dump_output: bool = typer.Option(
            False, "--dump", help="one .dump file: Aura's Import Database, Neo4j Desktop, "
            "neo4j-admin database load or upload"
        ),
        documents: Annotated[
            list[str] | None,
            typer.Option("--document", help="only this document (repeat for more); links "
                         "go only between documents that leave together"),
        ] = None,
    ) -> None:
        """Everything in the graph space -- every document, the links between them, the
        passages with their vectors and the indexes over them. By default a folder Neo4j's
        bulk import loads (`import.sh`, then `schema.cypher`)."""
        if corpus_service is None:
            raise typer.BadParameter("no graph space is configured")
        chosen = documents or None
        if json_output or cypher_output:
            # One file of the whole space: held whole to be written, so for a small one.
            records = corpus_service.records(chosen)
            output.write_text(
                json.dumps(records, ensure_ascii=False) if json_output
                else records_cypher(records, "// graph space"),
                encoding="utf-8",
            )
            written = sum(1 for node in records["nodes"] if "Document" in node["labels"])
        elif dump_output:
            with tempfile.TemporaryDirectory() as folder:
                written = corpus_service.bundle(Path(folder), chosen)
                corpus_service.dump(Path(folder), output)
        else:
            written = corpus_service.bundle(output, chosen)
        typer.echo(f"written={output} documents={written}")

    @app.command("build-graph", short_help="A folder's documents into the graph space, linked.")
    def build_graph_command(
        folder: Path,
        again: bool = typer.Option(
            False, "--again", help="read again files whose content is in the space already"
        ),
    ) -> None:
        """Every document in a folder into one graph: each read and put in the graph space,
        then the documents they name joined to the ones there. A file already in the space
        is passed over unless --again."""
        if corpus_service is None:
            raise typer.BadParameter("no graph space is configured")
        steps = {BuildStep.READ: "read", BuildStep.PUT: "put", BuildStep.LINK: "link"}
        report = corpus_service.build(
            folder, lambda step, done, total: typer.echo(f"{steps[step]} {done}/{total}"),
            again,
        )
        for path, reason in report.failed:
            typer.echo(f"failed={path} error={reason}")
        typer.echo(
            f"processed={report.processed} known={report.known} failed={len(report.failed)} "
            f"links_judged={report.links}"
        )
        if report.failed:
            raise typer.Exit(1)

    @app.command("delete-document", short_help="Delete documents from the graph space.")
    def delete_document(
        document_ids: list[str],
        yes: bool = typer.Option(False, "--yes", help="do not ask first"),
    ) -> None:
        """Delete documents from the graph space: every node, passage and link of theirs. A
        mention in another document that named one names whatever else answers it still."""
        if corpus_service is None:
            raise typer.BadParameter("no graph space is configured")
        files = corpus_service.files(document_ids)
        missing = [i for i in document_ids if i not in files]
        if missing:
            typer.echo(f"not in the graph space: {', '.join(missing)}", err=True)
            raise typer.Exit(1)
        for document_id, file in files.items():
            typer.echo(f"{document_id}  {file}")
        if not yes and not typer.confirm(f"delete these {len(files)} documents?"):
            raise typer.Exit(0)
        corpus_service.delete(list(files))
        typer.echo(f"deleted={len(files)}")

    @app.command("reset-graph", short_help="Empty the graph space: every document deleted.")
    def reset_graph(yes: bool = typer.Option(False, "--yes", help="do not ask first")) -> None:
        """Empty the graph space: every document, passage and link deleted, its indexes kept.
        What was built is read again from the files the next time they are built."""
        if corpus_service is None:
            raise typer.BadParameter("no graph space is configured")
        held = corpus_service.count()
        if not yes and not typer.confirm(f"delete all {held} documents from the graph space?"):
            raise typer.Exit(0)
        typer.echo(f"deleted={corpus_service.reset()}")

    @app.command("embed-graph", short_help="Embed passages for questions; link alike documents.")
    def embed_graph() -> None:
        """Embed every unit in the graph space not yet embedded, and index the vectors."""
        if rag_service is None:
            raise typer.BadParameter("no graph space is configured")
        embedded = rag_service.embed(lambda done, total: typer.echo(f"embedded {done}/{total}"))
        typer.echo(f"embedded={embedded}")

    @app.command(short_help="Answer a question from the graph space, with its sources.")
    def ask(
        question: str,
        top_k: int | None = typer.Option(
            None, "--top-k", help="passages found before the graph is walked (default: settings)"
        ),
    ) -> None:
        """Answer a question from the graph space (neo4j-graphrag over the vectors)."""
        if rag_service is None:
            raise typer.BadParameter("no graph space is configured")
        answer = rag_service.ask(question, top_k=top_k)
        typer.echo(_shown(answer.text))
        typer.echo("")
        for source in answer.sources:
            typer.echo(f"- [{_shown(source.file)}] {source.id} score={source.score:.3f}")

    @app.command(short_help="Serve the pipeline and the graph space over HTTP (/v1, /docs).")
    def serve(
        host: str = typer.Option("127.0.0.1", "--host"),
        port: int = typer.Option(8765, "--port"),
        web: Annotated[
            Path | None,
            typer.Option("--web", help="the built web app (default: the checkout's frontend/dist)"),
        ] = None,
        openapi: bool = typer.Option(False, "--openapi", help="print the API's contract, exit"),
    ) -> None:
        """The API at /v1, its contract at /docs, and the web app at / when there is one.
        Without GRAPHME_API_KEY it asks for no key, and so opens on this machine only."""
        # Here only: no other command waits for the web framework to load.
        from graphme.server import app as server
        from graphme.server.settings import LOOPBACK

        if openapi:
            typer.echo(server.openapi())
            return
        settings = load_settings()
        if host not in LOOPBACK and settings.server_key is None:
            typer.echo(f"GRAPHME_API_KEY를 설정하세요 -- 키 없는 서버는 {host}에 열지 않습니다"
                       " (이 컴퓨터만: --host 127.0.0.1)", err=True)
            raise typer.Exit(2)
        server.serve(settings, host=host, port=port, web=web)

    @app.command("app", short_help="Open the app: the server with the samples, in the browser.")
    def open_app(
        port: int | None = typer.Option(None, "--port", help="default: 8765 or the next free"),
        no_browser: bool = typer.Option(False, "--no-browser"),
    ) -> None:
        """What the installer's shortcut runs. Settings, graph data, the web app and the viewer
        are in the app's folder (GRAPHME_HOME, else the platform's app-data folder)."""
        from graphme.launcher import launch

        launch(port=port, browser=not no_browser)

    @app.command(short_help="Run an evaluation suite.")
    def evaluate(suite: Path) -> None:
        typer.echo(evaluation_service.evaluate(suite))

    @app.command("xlsx-bundle",
                 short_help="Render an XLSX workbook into a page and mapping bundle.")
    def xlsx_bundle(
        source: Path,
        output_directory: Path,
        viewer_directory: Annotated[
            Path | None, typer.Option("--viewer", help="OOXML viewer bundle directory")
        ] = None,
        timeout_seconds: float | None = typer.Option(None, "--timeout"),
        tile_size: int = typer.Option(1024, "--tile-size"),
    ) -> None:
        # The renderer the rest of the product uses: viewer and Chrome from .env.
        settings = load_settings()
        overrides = {
            "viewer_directory": viewer_directory, "render_timeout_seconds": timeout_seconds
        }
        settings = settings.model_copy(
            update={name: value for name, value in overrides.items() if value is not None}
        )
        result = XlsxBundleOrchestrator(
            browser_renderer=create_browser_renderer(settings), tile_size=tile_size
        ).build(source, output_directory)
        typer.echo(f"bundle={result.output_directory}")
        typer.echo(f"mapping={result.mapping_status}")
        typer.echo(f"pages={result.page_count}")
        typer.echo(f"nodes={result.node_count}")

    return app


_DIGEST = re.compile(r"\.emb[0-9a-f]{8}(?=\.[A-Za-z0-9]{1,8}\b)")


def _shown(text: str) -> str:
    """File names as a reader knows them: an attachment's digest (`embedded_name`) tells two
    files apart for the program and stays in the API, but means nothing printed."""
    return _DIGEST.sub("", text)


def _client() -> httpx.Client:
    return httpx.Client(timeout=MODEL_WAIT)


class _RuntimeQueryService:
    def query(self, *, document_id: str, question: str) -> Answer:
        with _client() as client:
            return create_runtime(client=client).query.query(
                document_id=document_id,
                question=question,
            )


class _RuntimeIngestService:
    def ingest(self, source: Path) -> IngestResult:
        with _client() as client:
            return create_runtime(client=client).ingest.ingest(source)


class _RuntimeInspectService:
    @staticmethod
    def _service() -> InspectionService:
        settings = load_settings()
        return InspectionService(store=create_store(settings))

    def inspect(self, document_id: str) -> str:
        return self._service().inspect(document_id)

    def inspect_record(self, document_id: str) -> InspectionReport:
        return self._service().inspect_record(document_id)

    def graph(self, document_id: str) -> ExtractedDocument:
        return self._service().graph(document_id)


class _RuntimeEvaluationService:
    def evaluate(self, suite: Path) -> str:
        settings = settings_for_evaluation(evaluation_profile(suite))
        with _client() as client:
            return create_runtime(settings=settings, client=client).evaluate.evaluate(suite)


class _RuntimeCorpusService:
    """The graph-space commands, each a `GraphMe` of its own: a command is one call."""

    def build(self, folder: Path, on_step: StepListener, again: bool = False) -> BuildReport:
        with GraphMe() as g:
            return g.add(folder, again=again, on_step=on_step)

    def records(
        self, document_ids: list[str] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        with GraphMe() as g:
            return g._graph_space().corpus(document_ids)

    def bundle(self, folder: Path, document_ids: list[str] | None = None) -> int:
        with GraphMe() as g:
            return g.export(folder, documents=document_ids)

    def dump(self, bundle: Path, target: Path) -> None:
        create_neo4j_admin(load_settings()).dump(bundle, target)

    def files(self, document_ids: list[str]) -> dict[str, str]:
        with GraphMe() as g:
            return g._graph_space().files(document_ids)

    def count(self) -> int:
        with GraphMe() as g:
            return len(g.documents())

    def reset(self) -> int:
        with GraphMe() as g:
            return g.reset()

    def delete(self, document_ids: list[str]) -> None:
        with GraphMe() as g:
            g.delete(document_ids)


class _RuntimeRagService:
    def embed(self, on_progress: Callable[[int, int], None]) -> int:
        with GraphMe() as g:
            return g.embed(on_progress=on_progress)

    def ask(self, question: str, *, top_k: int | None) -> GraphAnswer:
        with GraphMe() as g:
            return g.ask(question, top_k=top_k)


app = create_app(
    query_service=_RuntimeQueryService(),
    ingest_service=_RuntimeIngestService(),
    inspect_service=_RuntimeInspectService(),
    evaluation_service=_RuntimeEvaluationService(),
    corpus_service=_RuntimeCorpusService(),
    rag_service=_RuntimeRagService(),
)
