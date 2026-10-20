"""The HTTP API over the server's state (`state.py`) and the graph space: `/v1`, `/health`,
the contract at `/docs`, and the web app for every other path."""
from __future__ import annotations

import hmac
import json
import logging
import math
import shutil
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, TypeVar
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from graphme import __version__
from graphme.adapters.export.neo4j import graph_records, records_cypher, to_cypher
from graphme.adapters.export.neo4j_admin import AURA_URI
from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.page_renderer import PageRenderer
from graphme.adapters.rendering.visual_binding import page_name, placed_units
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.adapters.storage.graph_rag import MAX_TOP_K, GraphNotEmbedded, GraphRag
from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace, modified_at
from graphme.application.embedded_documents import name_part
from graphme.application.ingest import documents_in
from graphme.application.xlsx_visual_binding import CROP_ASSET_KEY, read_crop, read_node_crop
from graphme.config import Settings, load_settings
from graphme.runtime import create_browser_renderer
from graphme.server import models as api
from graphme.server.settings import changed, described, from_here, preflight_behind
from graphme.server.state import (
    BUILDING,
    IN_LOG,
    SPACE_WAIT,
    Busy,
    CorpusBuild,
    Demo,
    ExportJob,
    GraphStarting,
    Session,
    Undrawn,
)

WEB = Path(__file__).resolve().parents[3] / "frontend" / "dist"
"""The web app as a checkout builds it (`npm run build` in `frontend/`); an installed package
has none, and serves the API alone."""

MAX_QUESTION_CHARS = 1000
"""The longest question asked: a page of text is not a question, and it is what is embedded."""

T = TypeVar("T")

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
"""An upload is read whole into memory before it is written; this keeps that bounded."""

KEY_COOKIE = "graphme_key"
"""Where the web app keeps the key: an <img> or a download link cannot send a header."""

MAP_LIMIT = 300
"""The most documents the map draws: past it, the ones with the fewest links are left off."""


def errors(app: FastAPI) -> None:
    """Every answer that is not a success said as `{"detail": str}`: a request that cannot be
    read (400), this project's own refusals (`Busy`, `GraphNotEmbedded` 409, `Undrawn` 503),
    and anything else (500, the whole of it in the log). Python's own exceptions are not
    turned into anything here: only the route that called what documents them does that
    (`refused`), so a bug's KeyError is a 500, not a 404."""

    @app.exception_handler(RequestValidationError)
    async def unreadable(_: Request, error: RequestValidationError) -> JSONResponse:
        first = error.errors()[0] if error.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ())[1:])
        return JSONResponse({"detail": f"{where}: {first.get('msg', 'invalid')}"}, 400)

    @app.exception_handler(Busy)
    @app.exception_handler(GraphNotEmbedded)
    async def busy(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, 409)

    @app.exception_handler(Undrawn)
    async def undrawn(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, 503)

    @app.exception_handler(ServiceUnavailable)
    @app.exception_handler(SessionExpired)
    async def unreachable(_: Request, error: Exception) -> JSONResponse:
        # Reached once and lost since: `opened()` says it only when the space is first opened.
        logging.warning("the graph DB cannot be reached: %s", error)
        return JSONResponse({"detail": f"cannot connect to the graph DB {IN_LOG}"}, 503)

    @app.exception_handler(Exception)
    async def failed(_: Request, error: Exception) -> JSONResponse:
        # Logged again by the server once this answer is sent, with its traceback.
        return JSONResponse({"detail": f"the server could not handle this {IN_LOG}"}, 500)


@contextmanager
def refused() -> Iterator[None]:
    """What the call inside says it refuses, as HTTP: a value it does not take (400), a thing
    it does not hold (404). Wrapped around that call only."""
    try:
        yield
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except (LookupError, FileNotFoundError) as error:
        # A KeyError's own str() puts its message in quotes.
        said = error.args[0] if isinstance(error, KeyError) and error.args else error
        raise HTTPException(404, str(said)) from error


def create_app(
    demo: Demo,
    web: Path | None = None,
    *,
    load: Callable[[dict[str, str]], Settings] = load_settings,
    extra: APIRouter | None = None,
) -> FastAPI:
    """The server over `demo`, with the web app built in `web` -- or, without one, the API
    alone and `/` sent to its contract. `load` reads the settings a save in `PUT /v1/settings`
    is taken up with, as the server was started with them; `extra` is routes of the caller's
    own (the demo's samples), put under `/v1` behind the same key."""
    app = FastAPI(title="graphme", version=__version__)
    errors(app)

    def keyed(request: Request) -> None:
        """GRAPHME_API_KEY, when the server has one: as a bearer token or in the cookie."""
        key = demo.settings.server_key
        if key is None:
            # Keyless is this machine's only: not a name rebound to it, nor another site's page.
            if not from_here(request):
                raise HTTPException(403, "only this machine's pages may use it -- to use it"
                                         " from elsewhere, set GRAPHME_API_KEY")
            return
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        sent = (token if scheme.lower() == "bearer" else "",  # schemes ignore case (RFC 7235)
                request.cookies.get(KEY_COOKIE, ""))
        if not any(given and hmac.compare_digest(given.encode(), key.encode())
                   for given in sent):
            raise HTTPException(401, "an API key is required (GRAPHME_API_KEY)",
                                headers={"WWW-Authenticate": "Bearer"})

    v1 = APIRouter(prefix="/v1", dependencies=[Depends(keyed)])

    @app.get("/health", response_model=api.Health)
    def health() -> dict[str, str]:
        """That the server answers, and from which folder: another install on this machine
        (WSL's, seen from Windows) answers on the same port. Asks nothing of Neo4j or a model,
        and needs no key."""
        return {"status": "ok", "version": __version__, "home": str(Path.cwd())}

    # Settings -------------------------------------------------------------------------------

    def has_samples() -> bool:
        # An included router is kept whole in `app.routes`: its own paths are asked.
        return extra is not None and any(
            getattr(route, "path", None) == "/samples" for route in extra.routes)

    @v1.get("/settings", response_model=api.ServerSettings)
    def settings_now() -> dict[str, Any]:
        """What the server runs with -- a secret only as set or not -- and what keeps it from
        running."""
        return {**described(demo.settings), "samples": has_samples()}

    @v1.put("/settings", response_model=api.ServerSettings)
    def change_settings(change: api.SettingsChange, request: Request) -> dict[str, Any]:
        """Settings written to `.env` and taken up at once, each key tried first (no model is
        called). Only from this machine's own page, whatever the key: another site open in the
        browser could otherwise post keys here."""
        if not from_here(request):
            raise HTTPException(403, "settings can only be changed from this machine's page")
        with refused():
            new = changed(demo, change.values, Path.cwd() / ".env", load)
        return {**described(new), "samples": has_samples()}

    # Documents in the graph space ----------------------------------------------------------

    def opened() -> Neo4jGraphSpace:
        """The graph space; one that cannot be reached is said (503), not a crash."""
        try:
            return demo.space(wait=SPACE_WAIT)
        except GraphStarting as error:  # the page asks again on its own
            raise HTTPException(503, str(error), headers={"Retry-After": "3"}) from error
        except Exception as error:  # noqa: BLE001 -- said to the caller
            raise HTTPException(503, f"cannot connect to the graph DB: {error}") from error

    def unbuilt() -> None:
        if demo.build.state in BUILDING:
            raise HTTPException(409, "not while a graph is being built")

    def chosen(docs: list[str] | None) -> list[str] | None:
        """Which documents leave: all when none are named, never when an empty list is -- an
        export says what it carries."""
        if docs is None:
            return None
        if not docs:
            raise HTTPException(400, "choose at least one document to export")
        return docs

    @v1.get("/documents", response_model=api.Library)
    def library() -> dict[str, Any]:
        """Every document a reader can open, in one list: those in the graph space first --
        each matched, by its file, to the server's own reading of it when there is one -- then
        the files held here that are not in the space (yet, or that failed). `space_ok` is
        false when the graph DB cannot be reached; the rest is still listed."""
        catalog = list(demo.catalog.items())  # other threads add to it while this reads
        reading = {str(Path(path).resolve()): doc for doc, path in catalog}
        # The same file reached by two paths -- uploaded here, built from its own folder --
        # is one content: matched by it as well.
        by_content: dict[str | None, list[str]] = {}
        for doc, _ in catalog:
            by_content.setdefault(demo.content_hash(doc), []).append(doc)

        def state(doc: str | None) -> str:
            session = demo.sessions.get(doc) if doc else None
            return session.state if session else "idle"

        try:
            documents, _ = demo.space(wait=SPACE_WAIT).document_map()
            space_ok = True
        except GraphStarting as error:  # asked again, like the map, until it is up
            raise HTTPException(503, str(error), headers={"Retry-After": "3"}) from error
        except Exception:  # noqa: BLE001 -- the list still shows what the server holds
            documents, space_ok = [], False
        items: list[dict[str, Any]] = []
        matched: set[str] = set()
        for d in documents:
            path = d.get("source_path")
            same = by_content.get(d.get("content_hash"), []) if d.get("content_hash") else []
            file_id = (reading.get(str(Path(path).resolve())) if path else None) \
                or next((x for x in same if x in demo.sessions), same[0] if same else None)
            # Every reading of this content is this document, however the server reached it.
            matched.update([*same, *([file_id] if file_id else [])])
            items.append({"name": d["label"], "format": Path(d["label"]).suffix.lower().lstrip("."),
                          "in_space": True, "document_id": d["id"], "file_id": file_id,
                          "state": state(file_id), "modified": d.get("modified_at")})
        items += [
            {"name": Path(path).name, "format": Path(path).suffix.lower().lstrip("."),
             "in_space": False, "document_id": None, "file_id": doc, "state": state(doc),
             "modified": modified_at(path)}
            for doc, path in catalog if doc not in matched and doc in demo.sessions
        ]
        return {"items": items, "space_ok": space_ok}

    @v1.delete("/documents/{document_id}", response_model=api.Deleted)
    def delete_document(document_id: str) -> dict[str, str]:
        """A document gone from the graph space, with every node, passage and link of its. Not
        while a build is putting documents in and linking them."""
        unbuilt()
        opened()
        with refused():
            demo.graph.delete([document_id])
        demo.forget(document_id)
        return {"deleted": document_id}

    @v1.get("/documents/{document_id}/graph", response_model=api.DocumentGraph)
    def document_graph(document_id: str) -> dict[str, Any]:
        """One document of the space, with the passages it is cut into -- the outline draws
        each one's span."""
        space = opened()
        found = space.document_graph(document_id)
        if not found["nodes"]:
            raise HTTPException(404, "the document is not in the graph")
        return {**found, "chunks": space.chunk_spans(document_id)}

    @v1.post("/documents/{document_id}/file", response_model=api.PagesOf)
    def document_file(document_id: str) -> dict[str, str]:
        """The file that draws a document's pages, drawn again if this start has not: no
        model is called -- its outline is the one in the space."""
        opened()
        with refused():
            return {"file_id": demo.pages_of_document(document_id)}

    # The graph space as a whole -------------------------------------------------------------

    def space_records(docs: list[str] | None = None) -> dict[str, Any]:
        """The graph alone -- the chosen documents', or all -- without the passages and their
        vectors, which only the bundle and the dump carry."""
        records = opened().corpus(docs, passages=False)
        return {key: value for key, value in records.items() if key not in ("chunks", "covers")}

    @v1.get("/graph/map", response_model=api.CorpusMap)
    def graph_map() -> dict[str, Any]:
        """The documents in the space and how they are tied -- what the map draws."""
        space = opened()
        documents, links = space.document_map()
        similar = space.similar_links()
        # A map of thousands is drawn by nobody: the best-linked documents, and their lines.
        ties: dict[str, int] = {}
        for link in links:
            for end in (link["from"], link["to"]):
                ties[end] = ties.get(end, 0) + link["count"]
        for link in similar:
            for end in (link["from"], link["to"]):
                ties[end] = ties.get(end, 0) + 1
        kept = sorted(documents, key=lambda d: (-ties.get(d["id"], 0), d["label"] or ""))
        kept = kept[:MAP_LIMIT]
        on = {d["id"] for d in kept}
        # The file paths are this machine's; only the library matches on them.
        shown = [{k: v for k, v in d.items() if k != "source_path"}
                 for d in documents if d["id"] in on]
        return {"documents": shown, "total": len(documents),
                "links": [x for x in links if x["from"] in on and x["to"] in on],
                "similar": [x for x in similar if x["from"] in on and x["to"] in on]}

    @v1.get("/graph")
    def graph(docs: Annotated[list[str] | None, Query()] = None) -> dict[str, Any]:
        """The graph space, or the documents `docs` names, read back from it."""
        records = space_records(chosen(docs))
        documents = sum(1 for node in records["nodes"] if "Document" in node["labels"])
        return {**records, "pages": {}, "crops": [], "documents": documents}

    @v1.get("/graph.cypher")
    def graph_cypher(docs: Annotated[list[str] | None, Query()] = None) -> Response:
        return Response(
            records_cypher(space_records(chosen(docs)), "// graph space"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=corpus.cypher"},
        )

    @v1.post("/graph/reset", response_model=api.Reset)
    def reset(request: api.Confirm) -> dict[str, int]:
        """The graph space emptied, and what the server read let go with it: a folder put in
        afterwards is read afresh. Only when confirmed, and not while a build runs."""
        if not request.confirm:
            raise HTTPException(400, "confirm that the whole graph is to be deleted")
        unbuilt()
        opened()
        deleted = demo.graph.reset()
        demo.drop(list(demo.catalog))
        return {"deleted": deleted}

    @v1.get("/export/bundle")
    def export_bundle(docs: Annotated[list[str] | None, Query()] = None) -> FileResponse:
        """For a Neo4j of one's own: unzip, `./import.sh` with it stopped, then
        `schema.cypher` once it runs."""
        picked = chosen(docs)
        opened()
        archive = demo.bundle_zip(picked)
        return FileResponse(archive, filename="graphme-neo4j-import.zip",
                            background=BackgroundTask(shutil.rmtree, archive.parent, True))

    @v1.get("/documents/files.zip")
    def documents_zip(
        doc: Annotated[list[str], Query()],
        folder: Annotated[list[str], Query()],
        name: str = "documents",
    ) -> FileResponse:
        """The files the documents `doc` were read from, in one zip, each in the `folder` beside
        it (the page's folders of what a question found). A link, not a fetch: the app's own
        window saves it as it saves any download."""
        if len(doc) != len(folder):
            raise HTTPException(400, "each document needs one folder name")
        opened()
        archive = demo.files_zip(list(zip(doc, folder, strict=True)))
        shown = name_part(name[:80], "documents")
        return FileResponse(archive, filename=f"{shown}.zip", media_type="application/zip",
                            background=BackgroundTask(shutil.rmtree, archive.parent, True))

    # Files the server holds -----------------------------------------------------------------

    def ready(file_id: str) -> Session:
        with refused():
            session = demo.session(file_id)
        if not session.pages or (session.extracted is None and session.kept is None):
            raise HTTPException(409, "the file is not ready yet")
        return session

    def page_of(session: Session, index: int) -> RenderedPage:
        page = next((p for p in session.pages if p.index == index), None)
        if page is None:
            raise HTTPException(404, "this page cannot be found")
        return page

    @v1.post("/files", response_model=api.Uploaded)
    async def upload(
        file: UploadFile, modified: Annotated[float | None, Form()] = None
    ) -> dict[str, str]:
        """A file kept on the server -- nothing is read until a job asks. `modified` is the
        file's last change as the browser reads it, in milliseconds."""
        if modified is not None and not (math.isfinite(modified) and 0 <= modified < 2**53):
            raise HTTPException(400, "the modified time is not valid")
        # ponytail: Starlette spools the whole part to a temp file before this runs, so the
        # limit bounds memory, not disk; a Content-Length check before parsing if that matters.
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "the file is too large")
        return await run_in_threadpool(
            kept, file.filename or "document", content,
            modified / 1000 if modified is not None else None)

    def kept(name: str, content: bytes, modified: float | None) -> dict[str, str]:
        with refused():
            doc = demo.add(name, content, modified)
        if demo.already_in([doc]):
            return {"id": doc, "state": "in_space"}  # a build passes it over
        session = demo.sessions.get(doc)
        return {"id": doc, "state": session.state if session else "idle"}

    @v1.delete("/files/{file_id}", response_model=api.Deleted)
    def forget_file(file_id: str) -> dict[str, str]:
        """A file the server holds but the graph does not -- failed, or passed over -- taken
        off the list. Not while it is being read."""
        unbuilt()
        if file_id not in demo.catalog:
            raise HTTPException(404, "this file cannot be found")
        session = demo.sessions.get(file_id)
        if session is not None:
            with session.lock:
                if session.state not in ("idle", "error", "cancelled", "done", "rendered"):
                    raise HTTPException(409, "a file being read cannot be removed")
        demo.drop([file_id])
        return {"deleted": file_id}

    @v1.get("/files/{file_id}/pages", response_model=api.Pages)
    def pages(file_id: str) -> dict[str, Any]:
        with refused():
            session = demo.session(file_id)
        prepared = bool(session.pages) and (
            session.extracted is not None or session.kept is not None)
        return {
            "source": session.source.name,
            "format": session.format.value,
            "state": session.state,
            "error": session.error,
            "pending": bool(session.pending)
            or session.state
            in ("rendering", "grouping", "reading", "captioning", "referencing"),
            "sheets": [
                {
                    "index": page.index,
                    "name": page_name(page, session.format),
                    "width": page.width,
                    "height": page.height,
                    "derived": session.derived.get(page.index),
                    "seconds": session.seconds.get(page.index),
                    "pending": page.index in session.pending,
                    "error": session.errors.get(page.index),
                }
                for page in (session.pages if prepared else ())
            ],
        }

    @v1.get("/files/{file_id}/pages/{index}/image")
    def image(file_id: str, index: int) -> Response:
        """The page while its document is read; drawn for the reader, now, after that."""
        session = ready(file_id)
        path = session.workspace / f"page-{page_of(session, index).index:02d}.png"
        if path.is_file():
            return FileResponse(path, media_type="image/png")
        with refused():
            return Response(demo.preview(file_id, index), media_type="image/png")

    @v1.get("/files/{file_id}/pages/{index}/crop")
    def crop(file_id: str, index: int, node: str) -> Response:
        """The crop the binding stored for this node -- never a fresh cut of the page."""
        session = ready(file_id)
        if session.kept is not None:
            stored = session.kept.crops.get(node)
            assets = LocalAssetStore(demo.settings.asset_dir)
            picture = read_crop(stored, assets) if stored else None
        else:
            found = next((n for n in session.read().nodes if n.id == node), None)
            if found is None:
                raise HTTPException(404, "this node cannot be found")
            picture = (read_node_crop(found, asset_store=session.store)
                       if session.store is not None else None)
        if picture is None:
            raise HTTPException(404, "this node has no stored image")
        return Response(picture, media_type="image/png")

    @v1.get("/files/{file_id}/graph", response_model=api.DocumentGraph)
    def file_graph(file_id: str) -> dict[str, Any]:
        """The file as it was read, as Neo4j records, with the page each node is drawn on."""
        session = ready(file_id)
        if session.kept is not None:  # as the space holds it, outline and all
            return {**session.kept.records, "pages": session.kept.page_of,
                    "crops": list(session.kept.crops)}
        extracted = session.read()
        present = {n.id for n in extracted.nodes}
        return {
            **graph_records(extracted),
            # Measured before any outline; a node a reading dropped is no longer drawn.
            "pages": {n: p for n, p in session.page_of.items() if n in present},
            "crops": [n.id for n in extracted.nodes if n.metadata.get(CROP_ASSET_KEY)],
        }

    @v1.get("/files/{file_id}/boxes", response_model=api.Boxes)
    def boxes(file_id: str) -> dict[str, Any]:
        """Where each node is drawn on its page -- what the page shows boxed when a passage
        or a node is pointed at."""
        session = ready(file_id)
        if session.kept is not None:
            return {"boxes": {node: {"page": page, "box": box}
                              for node, (page, box) in session.kept.boxes.items()}}
        with session.lock:
            extracted, cached = session.read(), session.placed
        if cached is not None and cached[0] is extracted:
            placed = cached[1]
        else:
            # Measured outside the lock: a page's answer landing waits for nothing.
            placed = placed_units(extracted, session.pages, session.source)
            with session.lock:
                session.placed = (extracted, placed)
        return {"boxes": {node: {"page": page, "box": box} for node, (page, box) in placed.items()}}

    @v1.get("/files/{file_id}/graph.cypher")
    def file_cypher(file_id: str) -> Response:
        session = ready(file_id)
        return Response(
            records_cypher(session.kept.records, f"// {session.source.name}")
            if session.kept is not None else to_cypher(session.read()),
            media_type="text/plain; charset=utf-8",
            # A Korean file name is not latin-1, which is all a plain header value can hold.
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(f"{session.source.stem}.cypher")
            },
        )

    # Questions ------------------------------------------------------------------------------

    def asked(request: api.Question, run: Callable[[GraphRag, str, int], T]) -> T:
        """A question and its top_k checked, then `run` on the graph space's GraphRAG."""
        question = request.question.strip()
        if not question:
            raise HTTPException(400, "the question is empty")
        if len(question) > MAX_QUESTION_CHARS:
            raise HTTPException(
                400, f"the question is too long -- at most {MAX_QUESTION_CHARS:,} characters"
            )
        top_k = request.top_k if request.top_k is not None else demo.settings.retrieval_top_k
        if not 1 <= top_k <= MAX_TOP_K:
            raise HTTPException(400, f"top_k must be between 1 and {MAX_TOP_K}")
        opened()
        try:
            rag = demo.rag()
        except ValueError as error:  # a key or the graph DB not configured
            raise HTTPException(503, str(error)) from error
        try:
            return run(rag, question, top_k)
        except (GraphNotEmbedded, Neo4jError, ServiceUnavailable, SessionExpired):
            raise  # the graph DB's, not the model's: the handlers say them (409, 500)
        except Exception as error:  # noqa: BLE001 -- said to the caller, not a crash
            traceback.print_exc(file=sys.stderr)
            # What kind of failure it was; the whole of it, paths and all, is in the log.
            said = f"the question could not be answered ({type(error).__name__}) {IN_LOG}"
            raise HTTPException(502, said) from error

    @v1.post("/search", response_model=api.Found)
    def search(request: api.Question) -> dict[str, Any]:
        """What a question finds -- the passages and the documents linked to theirs, hop by
        hop -- with no answer written."""
        found = asked(request, lambda rag, question, top_k: rag.find(question, top_k=top_k))
        return {"sources": [asdict(source) for source in found]}

    @v1.post("/ask", response_model=api.Answer)
    def ask(request: api.Question) -> dict[str, Any]:
        """A question answered from the graph space, with the passages it was answered from."""
        answer = asked(request, lambda rag, question, top_k: rag.ask(question, top_k=top_k))
        return {"answer": answer.text, "sources": [asdict(source) for source in answer.sources]}

    # Jobs -----------------------------------------------------------------------------------

    def job_of(job_id: str) -> CorpusBuild | ExportJob:
        job = demo.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "this job cannot be found")
        return job

    def dump_ready(job: ExportJob) -> bool:
        """A dump done, and the latest export: the next one writes over its file."""
        return (job is demo.export and job.kind == "dump" and job.state == "done"
                and demo.dump_path.is_file())

    def said(job: CorpusBuild | ExportJob) -> dict[str, Any]:
        if isinstance(job, ExportJob):
            return {**asdict(job), "dump_ready": dump_ready(job)}
        return asdict(job)

    def started(job: CorpusBuild | ExportJob, response: Response) -> dict[str, Any]:
        response.headers["Location"] = f"/v1/jobs/{job.id}"
        return said(job)

    def held(files: list[str]) -> list[str]:
        known = [file for file in files if file in demo.catalog]
        if not known:
            raise HTTPException(400, "no file to read -- POST /v1/files first")
        return known

    @v1.post("/jobs/build", status_code=202, response_model=api.BuildState)
    def build(request: api.BuildRequest, response: Response) -> dict[str, Any]:
        """Files uploaded, or a folder on the server's machine, read and built into the graph
        space, then embedded. A folder is read with the server's own rights: the key is what
        grants them."""
        if folder := request.folder:
            try:
                where = Path(folder).expanduser().resolve()
                if not where.is_dir():
                    raise HTTPException(400, f"not a folder: {where}")
                found = documents_in(where)
            except (ValueError, OSError) as error:  # a NUL in the name, a folder it may not read
                raise HTTPException(400, f"the folder cannot be read: {folder}") from error
            # ponytail: the folder is walked inside the request; a job of its own if folders
            # large enough to keep the 202 waiting are built.
            docs = [demo.open_path(str(path)) for path in found]
            if not docs:
                raise HTTPException(400, "no readable document (xlsx, pptx, docx, pdf)")
            source = str(where)
        else:
            docs, source = held(request.files), ""
        return started(demo.build_corpus(docs, source, embed=request.embed), response)

    @v1.post("/jobs/read", status_code=202, response_model=api.BuildState)
    def read(request: api.ReadRequest, response: Response) -> dict[str, Any]:
        """Files read the way a build reads them -- pages, outline, captions -- and not put in
        the graph space: what `GET /v1/files/{id}/graph` then shows."""
        return started(demo.build_corpus(held(request.files), "", put=False), response)

    @v1.post("/jobs/embed", status_code=202, response_model=api.BuildState)
    def embed(response: Response) -> dict[str, Any]:
        """The passages of every document not embedded yet, cut and embedded."""
        return started(demo.start_embed(), response)

    @v1.post("/jobs/dump", status_code=202, response_model=api.ExportJob)
    def dump(response: Response, request: api.ExportRequest | None = None) -> dict[str, Any]:
        """A `.dump` for Aura's Import Database or Neo4j Desktop (`GET /v1/jobs/{id}/file`)."""
        docs = chosen(request.docs if request else None)
        return started(demo.start_export("dump", docs), response)

    @v1.post("/jobs/aura-upload", status_code=202, response_model=api.ExportJob)
    def aura_upload(request: api.AuraUpload, response: Response) -> dict[str, Any]:
        """The space pushed to an Aura instance with `neo4j-admin database upload`, replacing
        what it held. It leaves this machine, so only when confirmed."""
        uri = request.uri.strip()
        user = request.user.strip() or "neo4j"
        if not AURA_URI.match(uri):
            raise HTTPException(400, "an Aura address looks like neo4j+s://<id>.databases.neo4j.io")
        if not request.password:
            raise HTTPException(400, "the Aura password is required")
        if not request.confirm:
            raise HTTPException(400, "confirm that the data leaves for Neo4j's cloud")
        aura = {"uri": uri, "user": user, "password": request.password}
        return started(demo.start_export("aura-upload", chosen(request.docs), aura), response)

    @v1.get("/jobs", response_model=list[api.Job])
    def jobs() -> list[dict[str, Any]]:
        """Every job since this start, newest first."""
        return [said(job) for job in reversed(list(demo.jobs.values()))]

    @v1.get("/jobs/{job_id}", response_model=api.Job)
    def job(job_id: str) -> dict[str, Any]:
        return said(job_of(job_id))

    @v1.post("/jobs/{job_id}/cancel", response_model=api.Job)
    def cancel(job_id: str) -> dict[str, Any]:
        """A build or a reading stopped: what is already in the graph stays and is made
        searchable, the rest is passed over. A model call already sent finishes on its own;
        its answer is not used. Any other job: 409."""
        return said(demo.cancel(job_of(job_id)))

    @v1.get("/jobs/{job_id}/file")
    def job_file(job_id: str) -> FileResponse:
        found = job_of(job_id)
        if not isinstance(found, ExportJob) or not dump_ready(found):
            raise HTTPException(404, "this job has no dump -- not made yet, or an older one")
        return FileResponse(demo.dump_path, filename="neo4j.dump")

    if extra is not None:
        v1.include_router(extra)
    app.include_router(v1)

    # The web app: its built files, and its page for any other path -- the app routes itself.
    if web is not None and (web / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def page(path: str) -> Response:
        if path == "v1" or path.startswith("v1/"):
            raise HTTPException(404, "no such API path")
        if web is None:
            if path:
                raise HTTPException(404, "running without the web app -- the API is at /docs")
            return RedirectResponse("/docs")
        if path and "\x00" not in path:  # a NUL byte is no file name; the page answers it
            served = (web / path).resolve()
            if served.is_file() and served.is_relative_to(web.resolve()):
                return FileResponse(served)
        if not (web / "index.html").is_file():
            return HTMLResponse(NOT_BUILT, status_code=503)
        # Asked again each time, so a page never runs with files older than itself.
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    return app


NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Not built</title>
<body style="font:15px/1.6 system-ui;padding:40px;max-width:560px">
<h2>The web app is not built</h2>
<p><code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>, then reload this
page.</p>"""


def make_demo(settings: Settings, *, scratch: bool = False) -> Demo:
    """The server's state, drawing pages through the browser; its files under `data_dir`, or
    with `scratch` in a temporary folder of its own."""
    # ponytail: one folder for every server on this data_dir, no lock -- two running at once
    # share (and can overwrite) its uploads; a folder per process if that is wanted.
    root = None if scratch else settings.data_dir.resolve() / "server"
    return Demo(settings, PageRenderer(browser=create_browser_renderer(settings)), root=root)


def openapi() -> str:
    """The API's contract as JSON: what `npm run api` makes the web app's types from."""
    demo = make_demo(Settings(profile="test", embedding_provider="hash"), scratch=True)
    return json.dumps(create_app(demo).openapi(), ensure_ascii=False, indent=1)


def serve(settings: Settings, *, host: str, port: int, web: Path | None) -> None:
    """The server on `host`:`port` until it is stopped, its files under `data_dir`, with the
    web app built in `web` -- by default the checkout's, when there is one."""
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    preflight_behind(settings)
    demo = make_demo(settings)
    if web is None and WEB.is_dir():
        web = WEB
    uvicorn.run(create_app(demo, web=web), host=host, port=port, log_level="info")
