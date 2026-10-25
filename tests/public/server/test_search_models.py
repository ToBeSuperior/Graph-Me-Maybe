"""The search models run on this machine, over `/v1/search-models`: listed with whether each is
installed, installed and removed on request, the chosen one installed on its own and the space
embedded with it once it is. Invented data; nothing is fetched."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.config import Settings
from graphme.providers import LOCAL_EMBEDDERS, ModelFile
from graphme.server import create_app
from graphme.server.state import Demo
from tests.public.server.conftest import until


class FakeModels:
    """`LocalModels` without files: what is installed, held in a set."""

    def __init__(self, installed: set[str] | None = None) -> None:
        self.files = set(installed or ())
        self.gate = threading.Event()
        self.gate.set()

    def installed(self, model: ModelFile) -> bool:
        return model.file in self.files

    def install(self, model: ModelFile, on_progress: Any = None) -> None:
        if on_progress is not None:
            on_progress(1, 4)
        self.gate.wait(5)
        self.files.add(model.file)
        if on_progress is not None:
            on_progress(4, 4)

    def remove(self, model: ModelFile) -> None:
        self.files.discard(model.file)


def _file(name: str) -> str:
    row = LOCAL_EMBEDDERS[name].model_file
    assert row is not None
    return row.file


@pytest.fixture
def served(tmp_path: Path) -> Any:
    demo = Demo(Settings(embedding_provider="general"), renderer=None,  # type: ignore[arg-type]
                root=tmp_path / "server")
    models = FakeModels()
    demo.search_models.models = models  # type: ignore[assignment]
    embedded: list[str] = []
    demo.start_embed = lambda: embedded.append(demo.settings.embedding_provider)  # type: ignore[method-assign,assignment,return-value]
    return TestClient(create_app(demo), base_url="http://127.0.0.1:8765"), demo, models, embedded


def test_each_model_is_listed_with_whether_it_is_installed_and_which_is_chosen(
    served: Any,
) -> None:
    client, _, models, _ = served
    models.files.add(_file("korean"))

    said = {row["name"]: row for row in client.get("/v1/search-models").json()}

    assert set(said) == {"general", "korean"}
    assert said["general"]["installed"] is False and said["general"]["chosen"] is True
    assert said["korean"]["installed"] is True and said["korean"]["chosen"] is False
    assert said["korean"]["size"] == LOCAL_EMBEDDERS["korean"].model_file.size  # type: ignore[union-attr]


def test_a_model_installed_on_request_says_how_far_it_has_come(served: Any) -> None:
    client, _, models, _ = served
    models.gate.clear()

    started = client.post("/v1/search-models/korean/install")
    fetching = {r["name"]: r for r in client.get("/v1/search-models").json()}["korean"]
    models.gate.set()

    assert started.status_code == 202
    assert fetching["fetching"] == {"done": 1, "total": 4}
    assert until(lambda: models.installed(LOCAL_EMBEDDERS["korean"].model_file))  # type: ignore[arg-type]
    assert until(lambda: {r["name"]: r for r in client.get("/v1/search-models").json()}[
        "korean"]["fetching"] is None)


def test_a_model_is_removed_on_request_but_never_the_one_searched_with(served: Any) -> None:
    client, _, models, _ = served
    models.files |= {_file("general"), _file("korean")}

    assert client.delete("/v1/search-models/korean").status_code == 200
    assert client.delete("/v1/search-models/general").status_code == 409

    assert models.files == {_file("general")}


def test_the_chosen_model_is_installed_on_its_own_and_the_space_embedded_with_it(
    served: Any,
) -> None:
    _, demo, models, embedded = served

    demo.search_models.ensure_chosen()

    assert until(lambda: embedded == ["general"])
    assert _file("general") in models.files


def test_switching_to_an_installed_model_embeds_the_space_again_with_it(
    served: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, demo, models, embedded = served
    models.files.add(_file("korean"))

    demo.reconfigure(demo.settings.model_copy(update={"embedding_provider": "korean"}))

    assert until(lambda: embedded == ["korean"])


def test_switching_to_a_model_not_installed_installs_it_first(served: Any) -> None:
    _, demo, models, embedded = served

    demo.reconfigure(demo.settings.model_copy(update={"embedding_provider": "korean"}))

    assert until(lambda: embedded == ["korean"])
    assert _file("korean") in models.files and _file("general") not in models.files


def test_an_unknown_model_is_a_404(served: Any) -> None:
    client, *_ = served

    assert client.post("/v1/search-models/nope/install").status_code == 404


def test_the_server_shutting_down_stops_the_model_servers_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # uvicorn raises the signal it was stopped by again once it has shut down, so no exit
    # handler runs: a model server left behind ran on with nothing to stop it.
    from graphme import runtime

    stopped: list[bool] = []
    monkeypatch.setattr(runtime, "stop_local_models", lambda: stopped.append(True))
    demo = Demo(Settings(profile="test", embedding_provider="hash"), renderer=None,  # type: ignore[arg-type]
                root=tmp_path / "server")

    with TestClient(create_app(demo)):
        assert stopped == []

    assert stopped == [True]
