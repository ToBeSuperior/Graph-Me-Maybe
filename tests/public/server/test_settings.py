"""Keys and model settings over `/v1/settings`, the server's own key, and what a request that
cannot be read or a bug is answered with."""
from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme import model_endpoint
from graphme.server import create_app
from graphme.server import settings as server_settings
from graphme.server.state import Demo

KEY = model_endpoint.PROVIDERS[model_endpoint.DEFAULT_PROVIDER].key_env
EMBED_KEY = model_endpoint.EMBEDDERS["openai"].key_env


@pytest.fixture
def page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The settings page of a server started in `tmp_path`, whose `.env` is there."""
    from graphme.config import Settings

    refused: set[str] = set()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        server_settings, "_models",
        lambda url, headers: "refused" if any(
            key in value for value in headers.values() for key in refused) else None)
    for name in (KEY, EMBED_KEY, "GRAPHME_PROVIDER", "GRAPHME_EMBEDDING_PROVIDER",
                 "GRAPHME_MODEL", "GRAPHME_MODEL_BASE_URL", "GRAPHME_HIERARCHY_READINGS",
                 "GRAPHME_API_KEY", "GRAPHME_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("# kept\nGRAPHME_MODEL=old\n" + f"{EMBED_KEY}=\n")
    demo = Demo(Settings(), renderer=None, root=tmp_path / "server")  # type: ignore[arg-type]
    client = TestClient(create_app(demo), base_url="http://127.0.0.1:8765")
    return client, demo, tmp_path / ".env", refused


@pytest.mark.skipif(KEY == EMBED_KEY, reason="one variable holds both keys in the public build")
def test_saved_settings_go_into_env_and_are_taken_up_at_once(page: Any) -> None:
    client, demo, env, _ = page
    said = client.put("/v1/settings", json={"values": {
        EMBED_KEY: "sk-proj-abcdefgh1234", "GRAPHME_MODEL": "new-model", KEY: ""}})

    assert said.status_code == 200, said.text
    text = env.read_text()
    assert text.startswith("# kept\n") and "GRAPHME_MODEL=new-model\n" in text
    assert f"{EMBED_KEY}=sk-proj-abcdefgh1234\n" in text and KEY not in text
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert demo.settings.embedding_key == "sk-proj-abcdefgh1234"
    assert demo.settings.model == "new-model" and demo.graph.settings is demo.settings
    shown = client.get("/v1/settings")
    assert "sk-proj-abcdefgh1234" not in shown.text and shown.json()["samples"] is False
    fields = {f["env"]: f for f in shown.json()["fields"]}
    assert fields[EMBED_KEY] == {
        "env": EMBED_KEY, "secret": True, "set": True, "value": None, "hint": "1234"}


def test_a_refused_key_is_not_saved(page: Any) -> None:
    client, demo, env, refused = page
    refused.add("sk-wrong-key-0000")
    before = env.read_text()
    said = client.put("/v1/settings", json={"values": {EMBED_KEY: "sk-wrong-key-0000"}})
    assert said.status_code == 400 and env.read_text() == before
    assert demo.settings.embedding_key is None


@pytest.mark.parametrize("headers", [{"origin": "https://evil.example"}, {"host": "evil.example"}])
def test_only_this_machines_page_may_change_them(page: Any, headers: dict[str, str]) -> None:
    client, _, env, _ = page
    before = env.read_text()
    said = client.put("/v1/settings", headers=headers, json={"values": {"GRAPHME_MODEL": "x"}})
    assert said.status_code == 403 and env.read_text() == before


def test_unknown_names_newlines_and_shell_overrides_are_refused(
    page: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _, _ = page
    for values in ({"GRAPHME_NEO4J_PASSWORD": "x"}, {"GRAPHME_MODEL": "a\nB=1"}):
        assert client.put("/v1/settings", json={"values": values}).status_code == 400
    monkeypatch.setenv("GRAPHME_MODEL", "shell")
    said = client.put("/v1/settings", json={"values": {"GRAPHME_MODEL": "x"}})
    assert said.status_code == 400 and "환경변수" in said.json()["detail"]


def test_settings_are_not_changed_while_a_build_runs(page: Any) -> None:
    client, demo, env, _ = page
    before = env.read_text()
    demo.build.state = "reading"
    said = client.put("/v1/settings", json={"values": {"GRAPHME_MODEL": "x"}})
    assert said.status_code == 409 and env.read_text() == before


def test_a_request_that_cannot_be_read_is_a_400(page: Any) -> None:
    client, _, _, _ = page
    said = client.put("/v1/settings", json={"values": "not a mapping"})
    assert said.status_code == 400 and said.json()["detail"].startswith("values")


# The server's key ---------------------------------------------------------------------------


def _keyed(demo: Demo, key: str | None, web: Path | None = None) -> TestClient:
    demo.settings = demo.settings.model_copy(update={"server_key": key})
    return TestClient(create_app(demo, web=web), base_url="http://127.0.0.1:8765")


def test_without_a_key_the_api_asks_for_none(page: Any) -> None:
    _, demo, _, _ = page
    assert _keyed(demo, None).get("/v1/settings").status_code == 200


def test_with_a_key_every_api_path_asks_for_it(page: Any) -> None:
    _, demo, _, _ = page
    client = _keyed(demo, "invented-server-key")

    told = client.get("/v1/settings")
    assert told.status_code == 401 and told.json()["detail"]
    assert client.put("/v1/settings", json={"values": {}}).status_code == 401
    for wrong in ("Bearer wrong", "invented-server-key", "Bearer "):
        assert client.get("/v1/settings", headers={"Authorization": wrong}).status_code == 401
    for right in ("Bearer invented-server-key", "bearer invented-server-key"):
        assert client.get("/v1/settings", headers={"Authorization": right}).status_code == 200
    client.cookies.set("graphme_key", "invented-server-key")
    assert client.get("/v1/settings").status_code == 200, "the web app's <img> sends the cookie"
    told = client.put("/v1/settings", headers={"origin": "https://evil.example"},
                      json={"values": {"GRAPHME_MODEL": "x"}})
    assert told.status_code == 403, "the key is no licence to change keys from another site"


def test_with_a_key_the_health_the_contract_and_the_web_app_stay_open(
    page: Any, tmp_path: Path
) -> None:
    _, demo, _, _ = page
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("<div id=root></div>")
    client = _keyed(demo, "invented-server-key", web=web)

    for path in ("/health", "/docs", "/openapi.json", "/", "/manage"):
        assert client.get(path).status_code == 200, path


def test_a_key_is_read_from_the_environment_and_never_from_yaml(tmp_path: Path) -> None:
    from graphme.config import load_settings

    assert load_settings({"GRAPHME_API_KEY": "invented"}).server_key == "invented"
    assert "invented" not in repr(load_settings({"GRAPHME_API_KEY": "invented"}))
    config = tmp_path / "graphme.yaml"
    config.write_text("server_key: invented\n")
    with pytest.raises(ValueError, match="server_key"):
        load_settings({"GRAPHME_CONFIG": str(config)})


def test_a_bug_is_a_500_said_plainly_never_a_404(
    page: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python's own exceptions mean something only where the call documents them: a KeyError
    from a bug is the server's failure, and what it was stays in the log."""
    _, demo, _, _ = page

    def broken(url: str, headers: dict[str, str]) -> str:
        raise KeyError("/private/path/of/this/machine")

    monkeypatch.setattr(server_settings, "_models", broken)
    told = TestClient(create_app(demo), base_url="http://127.0.0.1:8765",
                      raise_server_exceptions=False).get("/v1/settings")

    assert told.status_code == 500
    assert "/private/path" not in told.json()["detail"]


def test_without_a_key_only_this_machines_pages_reach_the_api(page: Any) -> None:
    """A keyless server is this machine's: a name rebound to 127.0.0.1, or another site's
    page posting to it, is turned away -- a folder build would read this machine's files."""
    _, demo, _, _ = page

    def no_space() -> None:
        # Only the gate is asked here: a real space would start Docker's Neo4j on the machine
        # running the tests, and its log landed in another test's output.
        raise RuntimeError("no graph space in this test")

    demo.space = no_space  # type: ignore[assignment,method-assign]
    rebound = TestClient(create_app(demo), base_url="http://evil.example:8765")
    here = TestClient(create_app(demo), base_url="http://127.0.0.1:8765")

    assert rebound.get("/v1/documents").status_code == 403
    assert rebound.get("/health").status_code == 200
    told = here.post("/v1/jobs/embed", headers={"origin": "https://evil.example"})
    assert told.status_code == 403 and told.json()["detail"]
    assert here.get("/v1/documents", headers={"origin": "http://localhost:5173"}).status_code != 403


def test_a_save_is_taken_up_as_the_server_was_started(page: Any) -> None:
    """The demo reads every page twice unless told otherwise: a key saved from its page must
    not quietly drop it back to the plain default."""
    _, demo, _, _ = page
    from graphme.config import load_settings

    def twice(environ: dict[str, str]) -> Any:
        return load_settings(environ).model_copy(update={"hierarchy_readings": 2})

    client = TestClient(create_app(demo, load=twice), base_url="http://127.0.0.1:8765")
    said = client.put("/v1/settings", json={"values": {"GRAPHME_MODEL": "new-model"}})

    assert said.status_code == 200, said.text
    assert demo.settings.hierarchy_readings == 2 and demo.settings.model == "new-model"


def test_the_page_offers_the_chosen_providers_keys_and_never_their_values(page: Any) -> None:
    client, demo, _, _ = page
    said = client.get("/v1/settings").json()
    names = [field["env"] for field in said["fields"]]

    assert names[0] == KEY and names.count(EMBED_KEY) == 1
    assert {"GRAPHME_PROVIDER", "GRAPHME_EMBEDDING_PROVIDER", "GRAPHME_MODEL_BASE_URL",
            "GRAPHME_MODEL", "GRAPHME_HIERARCHY_READINGS"} <= set(names)
    assert said["endpoint"] == demo.settings.provider
    assert all(field["value"] is None for field in said["fields"] if field["secret"])


def test_saving_under_a_test_embedder_has_no_embedding_key_to_check(
    page: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, env, _ = page
    monkeypatch.setenv("GRAPHME_PROFILE", "test")
    monkeypatch.setenv("GRAPHME_EMBEDDING_PROVIDER", "hash")

    said = client.put("/v1/settings", json={"values": {"GRAPHME_MODEL": "other"}})

    assert said.status_code == 200, said.text
    assert "GRAPHME_MODEL=other\n" in env.read_text()


@pytest.mark.skipif(KEY == EMBED_KEY, reason="the chat key is tried at the same address")
def test_an_empty_embedding_key_is_not_tried(page: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from graphme.config import load_settings

    _, demo, _, _ = page
    sent: list[str] = []
    monkeypatch.setattr(server_settings, "_models",
                        lambda url, headers: sent.append(url))  # type: ignore[func-returns-value]

    def keyless(environ: dict[str, str]) -> Any:
        return load_settings(environ).model_copy(update={"embedding_key": None})

    client = TestClient(create_app(demo, load=keyless), base_url="http://127.0.0.1:8765")
    client.put("/v1/settings", json={"values": {EMBED_KEY: "sk-proj-abcdefgh1234"}})

    assert model_endpoint.EMBEDDERS["openai"].base_url not in sent


@pytest.fixture
def two_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    from graphme.providers import Provider

    monkeypatch.setitem(model_endpoint.PROVIDERS, "other", Provider(
        wire="chat_completions", base_url="http://127.0.0.1:9/v1", key_env=KEY,
        model="other-default"))


def test_switching_provider_drops_the_old_providers_address_and_model(
    page: Any, two_providers: None
) -> None:
    client, demo, env, _ = page
    env.write_text(env.read_text() + "GRAPHME_MODEL_BASE_URL=http://127.0.0.1:7/v1\n")

    said = client.put("/v1/settings", json={"values": {"GRAPHME_PROVIDER": "other"}})

    assert said.status_code == 200, said.text
    text = env.read_text()
    assert "GRAPHME_MODEL=" not in text and "GRAPHME_MODEL_BASE_URL=" not in text
    assert "GRAPHME_PROVIDER=other\n" in text and text.startswith("# kept\n")
    assert demo.settings.base_url == "http://127.0.0.1:9/v1"
    assert demo.settings.model == "other-default"


def test_switching_provider_keeps_what_the_same_save_sets(page: Any, two_providers: None) -> None:
    client, demo, env, _ = page

    said = client.put("/v1/settings", json={"values": {
        "GRAPHME_PROVIDER": "other", "GRAPHME_MODEL": "chosen"}})

    assert said.status_code == 200, said.text
    assert "GRAPHME_MODEL=chosen\n" in env.read_text()
    assert demo.settings.model == "chosen"


def test_saving_the_same_provider_keeps_the_stored_model(page: Any) -> None:
    client, demo, env, _ = page

    said = client.put("/v1/settings", json={"values": {
        "GRAPHME_PROVIDER": demo.settings.provider}})

    assert said.status_code == 200, said.text
    assert "GRAPHME_MODEL=old\n" in env.read_text()


def test_switching_provider_under_a_shell_pinned_address_is_refused(
    page: Any, two_providers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, demo, env, _ = page
    monkeypatch.setenv("GRAPHME_MODEL_BASE_URL", "http://127.0.0.1:7/v1")
    before = env.read_text()

    said = client.put("/v1/settings", json={"values": {"GRAPHME_PROVIDER": "other"}})

    assert said.status_code == 400 and "GRAPHME_MODEL_BASE_URL" in said.json()["detail"]
    assert env.read_text() == before and demo.settings.provider != "other"


def test_switching_provider_under_a_config_file_model_is_refused(
    page: Any, two_providers: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, demo, _, _ = page
    config = tmp_path / "graphme.yaml"
    config.write_text("model: pinned-model\n")
    monkeypatch.setenv("GRAPHME_CONFIG", str(config))

    said = client.put("/v1/settings", json={"values": {"GRAPHME_PROVIDER": "other"}})

    assert said.status_code == 400 and "GRAPHME_MODEL" in said.json()["detail"]
    assert "GRAPHME_CONFIG" in said.json()["detail"]
    assert demo.settings.provider != "other"
