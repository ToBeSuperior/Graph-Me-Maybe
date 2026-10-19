"""The demo's additions to the server: sample documents to try it on, two readings of every
page and ten minutes to draw one, and Neo4j warmed at start. `graphme app` and
`tools/hierarchy_demo/app.py` both run it."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from graphme.config import Settings, load_settings
from graphme.samples import write_samples
from graphme.server import create_app
from graphme.server.app import WEB, make_demo
from graphme.server.models import BuildState
from graphme.server.settings import preflight
from graphme.server.state import BUILDING, Demo


class Samples(BaseModel):
    files: list[str]
    build: BuildState


def samples(demo: Demo) -> APIRouter:
    """`POST /v1/samples`: the sample documents (`samples.py`) written on this machine and
    built into the graph -- something to try the demo on before one's own. Written the same
    each time, so asked for again they are found in the graph rather than read twice."""
    router = APIRouter()

    @router.post("/samples", response_model=Samples)
    def corpus_samples() -> dict[str, Any]:
        if demo.build.state in BUILDING:  # its files may be being read: not written over
            raise HTTPException(409, "a graph build is already running")
        written = write_samples(demo.root / "samples")
        files = [demo.open_path(str(path)) for path in written]
        return {"files": [path.name for path in written],
                "build": asdict(demo.build_corpus(files, ""))}

    return router


def demo_settings(environ: dict[str, str] | None = None) -> Settings:
    """The settings build-graph reads, but two readings of every page and ten minutes to draw
    one unless they say otherwise -- where this demo differs from build-graph on purpose."""
    settings = load_settings(environ)
    update: dict[str, Any] = {}
    if "hierarchy_readings" not in settings.model_fields_set:
        update["hierarchy_readings"] = 2
    if "render_timeout_seconds" not in settings.model_fields_set:
        update["render_timeout_seconds"] = 600.0
    return settings.model_copy(update=update)


def build_app(demo: Demo, web: Path | None) -> FastAPI:
    """The server over `demo`, with the samples and the demo's own settings. Without `web`,
    a checkout's build (the server's own default) if there is one."""
    return create_app(demo, web=web or (WEB if WEB.is_dir() else None),
                      load=demo_settings, extra=samples(demo))


def run(port: int, web: Path | None, *, on_ready: Callable[[], None] | None = None) -> None:
    """The server with the samples on 127.0.0.1:`port`, settings from the working directory's
    `.env`; `on_ready` is called once it answers."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = demo_settings()
    for problem in preflight(settings):
        logging.warning("not ready: %s", problem)
    demo = make_demo(settings)

    def warm() -> None:
        # The graph space -- its Neo4j started on first use -- opened while the page loads.
        try:
            demo.space()
        except Exception as error:  # noqa: BLE001
            logging.warning("graph space not open yet: %s", error)

    threading.Thread(target=warm, daemon=True).start()
    config = uvicorn.Config(build_app(demo, web), host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    if on_ready is not None:
        def when_up() -> None:
            while not server.started:
                time.sleep(0.1)
            on_ready()
        threading.Thread(target=when_up, daemon=True).start()
    server.run()
