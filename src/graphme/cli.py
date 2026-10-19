from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Protocol

import typer

from graphme.adapters.export.neo4j import records_cypher
from graphme.adapters.storage.graph_rag import GraphAnswer
from graphme.api import GraphMe
from graphme.application.corpus_build import (
    BuildReport,
    BuildStep,
    StepListener,
)
from graphme.application.embedded_documents import shown_name
from graphme.application.xlsx_bundle import XlsxBundleOrchestrator
from graphme.config import load_settings
from graphme.runtime import create_browser_renderer


class CorpusService(Protocol):
    """The graph space: a folder built into it, and what it holds."""

    def build(self, folder: Path, on_step: StepListener, again: bool = False) -> BuildReport: ...

    def records(
        self, document_ids: list[str] | None = None
    ) -> dict[str, list[dict[str, Any]]]: ...

    def export(
        self, target: Path, document_ids: list[str] | None = None, *, dump: bool = False
    ) -> int:
        """The import bundle written into `target`, read from the space as it is written --
        or with `dump`, one `.dump` file made from it by `neo4j-admin`; how many documents
        left."""
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
    corpus_service: CorpusService | None = None,
    rag_service: RagService | None = None,
) -> typer.Typer:
    app = typer.Typer()

    @app.callback()
    def main() -> None:
        pass

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
        else:
            if dump_output:
                # One file: into a folder as graph.dump, else with its extension.
                output = output / "graph.dump" if output.is_dir() else output.with_suffix(".dump")
            written = corpus_service.export(output, chosen, dump=dump_output)
        typer.echo(f"written={output} documents={written}")

    @app.command("build-graph", short_help="A folder's documents into the graph space, linked.")
    def build_graph_command(
        folder: Path,
        again: bool = typer.Option(
            False, "--again", help="read again files whose content is in the space already"
        ),
        no_embed: bool = typer.Option(
            False, "--no-embed", help="leave the passages unembedded (embed-graph does it later)"
        ),
    ) -> None:
        """Every document in a folder into one graph: each read and put in the graph space,
        then the documents they name joined to the ones there, then embedded for questions as
        the app does. A file already in the space is passed over unless --again."""
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
        if rag_service is not None and not no_embed:
            embedded = rag_service.embed(
                lambda done, total: typer.echo(f"embedded {done}/{total}"))
            typer.echo(f"embedded={embedded}")
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
        typer.echo(shown_name(answer.text))
        typer.echo("")
        for source in answer.sources:
            typer.echo(f"- [{shown_name(source.file)}] {source.id} score={source.score:.3f}")

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
        from graphme.config import LOOPBACK
        from graphme.server import app as server

        if openapi:
            typer.echo(server.openapi())
            return
        settings = load_settings()
        if host not in LOOPBACK and settings.server_key is None:
            typer.echo(f"set GRAPHME_API_KEY -- a server without a key is not opened on {host}"
                       " (this machine only: --host 127.0.0.1)", err=True)
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


class _RuntimeCorpusService:
    """The graph-space commands, each a `GraphMe` of its own: a command is one call."""

    def build(self, folder: Path, on_step: StepListener, again: bool = False) -> BuildReport:
        with GraphMe() as g:
            return g.add(folder, again=again, on_step=on_step)

    def records(
        self, document_ids: list[str] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        with GraphMe() as g:
            return g.graph_space().corpus(document_ids)

    def export(
        self, target: Path, document_ids: list[str] | None = None, *, dump: bool = False
    ) -> int:
        with GraphMe() as g:
            return g.export(target, dump=dump, documents=document_ids)

    def files(self, document_ids: list[str]) -> dict[str, str]:
        with GraphMe() as g:
            return g.graph_space().files(document_ids)

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
    corpus_service=_RuntimeCorpusService(),
    rag_service=_RuntimeRagService(),
)


def main() -> None:
    """The installed command: what keeps a command from running is one line, not a traceback."""
    try:
        app()
    except (ValueError, RuntimeError, OSError) as error:
        typer.echo(f"error: {error}", err=True)
        raise SystemExit(1) from None
