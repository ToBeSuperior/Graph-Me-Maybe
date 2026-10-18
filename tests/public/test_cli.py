from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner


def test_exported_app_exposes_all_commands_without_provider_calls() -> None:
    from graphme.cli import app

    invocation = CliRunner().invoke(app, ["--help"])

    assert invocation.exit_code == 0
    for command in ("build-graph", "embed-graph", "ask", "export-corpus", "xlsx-bundle"):
        assert command in invocation.stdout
    for command in ("ingest", "query", "inspect", "evaluate", "export-neo4j"):
        assert f" {command} " not in invocation.stdout


def test_export_corpus_writes_what_the_graph_space_holds(tmp_path: Path) -> None:
    from graphme.cli import create_app

    dumped: list[Path] = []
    chosen: list = []

    class _Corpus:
        def export(self, target: Path, document_ids=None, *, dump: bool = False) -> int:
            from graphme.adapters.export.neo4j_import import write_import_bundle
            from tests.fakes.corpus import rows_of

            folder = target.with_suffix(".bundle") if dump else target
            write_import_bundle(rows_of(self.records(document_ids)), folder)
            if dump:
                dumped.append(folder / "import.sh")
                target.write_bytes(b"dump")
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


def test_a_command_that_cannot_run_says_why_in_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from graphme import cli

    def refuse() -> None:
        raise ValueError("OPENAI_API_KEY is required (GRAPHME_PROVIDER=openai)")

    monkeypatch.setattr(cli, "app", refuse)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    said = capsys.readouterr().err
    assert said.strip().splitlines() == [
        "오류: OPENAI_API_KEY is required (GRAPHME_PROVIDER=openai)"]


def test_build_graph_embeds_what_it_read_so_questions_work_at_once(tmp_path: Path) -> None:
    # Built from the command line, a question was refused (409) until `embed-graph` ran.
    from graphme.application.corpus_build import BuildReport
    from graphme.cli import create_app

    embedded: list[str] = []

    class _Corpus:
        def build(self, folder: Path, on_step, again=False) -> BuildReport:
            return BuildReport(processed=1, failed=(), links=0)

    class _Rag:
        def embed(self, on_progress) -> int:
            embedded.append("all")
            on_progress(3, 3)
            return 3

    app = create_app(corpus_service=_Corpus(), rag_service=_Rag())
    done = CliRunner().invoke(app, ["build-graph", str(tmp_path)])
    assert done.exit_code == 0, done.output
    assert embedded == ["all"] and "embedded=3" in done.output
    skipped = CliRunner().invoke(app, ["build-graph", str(tmp_path), "--no-embed"])
    assert skipped.exit_code == 0 and embedded == ["all"]
