"""Settings: what is read from where, and what is refused -- the same in either build."""
import logging
from pathlib import Path

import pytest

from graphme import model_endpoint
from graphme.config import Settings, load_settings


def test_defaults_come_from_the_chosen_provider_and_embedder() -> None:
    settings = load_settings(environ={})
    chat = model_endpoint.PROVIDERS[model_endpoint.DEFAULT_PROVIDER]
    embedder = model_endpoint.EMBEDDERS[settings.embedding_provider]

    assert settings.provider == model_endpoint.DEFAULT_PROVIDER
    assert (settings.base_url, settings.model) == (chat.base_url.rstrip("/"), chat.model)
    assert (settings.embedding_model, settings.embedding_dimensions) == (
        embedder.model, embedder.dimensions)


def test_an_unknown_embedder_is_refused() -> None:
    with pytest.raises(ValueError, match="GRAPHME_EMBEDDING_PROVIDER"):
        Settings(embedding_provider="voyage")


def test_given_values_beat_the_provider_defaults() -> None:
    settings = Settings(model="mine", embedding_model="embed-mine", embedding_dimensions=4)

    assert (settings.model, settings.embedding_model, settings.embedding_dimensions) == (
        "mine", "embed-mine", 4)


def test_an_empty_key_is_no_key() -> None:
    embedder = model_endpoint.EMBEDDERS["openai"]
    settings = load_settings(environ={embedder.key_env: ""})

    assert settings.embedding_key is None


@pytest.mark.parametrize("secret", ["api_key", "embedding_key", "neo4j_password"])
def test_yaml_config_cannot_carry_secrets(tmp_path: Path, secret: str) -> None:
    config_file = tmp_path / "graphme.yaml"
    config_file.write_text(f"{secret}: leaked\n", encoding="utf-8")

    with pytest.raises(ValueError, match="secrets must be supplied by environment"):
        load_settings(environ={"GRAPHME_CONFIG": str(config_file)})


def test_fake_embedding_provider_requires_test_profile() -> None:
    with pytest.raises(ValueError, match="GRAPHME_PROFILE=test"):
        Settings(embedding_provider="fake", profile="local")

    ok = Settings(embedding_provider="fake", profile="test")
    assert ok.embedding_provider == "fake"


def test_hash_embedding_provider_requires_test_profile() -> None:
    with pytest.raises(ValueError, match="GRAPHME_PROFILE=test"):
        Settings(embedding_provider="hash", profile="local")

    ok = Settings(embedding_provider="hash", profile="test")
    assert ok.embedding_provider == "hash"


def test_dotenv_is_loaded_without_overriding_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "GRAPHME_PROFILE=test\n"
        "GRAPHME_EMBEDDING_PROVIDER=hash\n"
        "GRAPHME_EMBEDDING_DIMENSIONS=16\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GRAPHME_EMBEDDING_DIMENSIONS", "8")

    settings = load_settings()

    assert settings.profile == "test"
    assert settings.embedding_provider == "hash"
    assert settings.embedding_dimensions == 8


def test_explicit_environment_does_not_load_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GRAPHME_PROFILE=test\n", encoding="utf-8")

    assert load_settings(environ={}).profile == "local"


def test_non_integer_setting_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be an integer"):
        load_settings(environ={"GRAPHME_RRF_K": "many"})


def test_viewer_settings_are_typed_and_overridable(tmp_path: Path) -> None:
    # The OOXML renderer needs a bundle on disk; nothing renders DOCX, PPTX or XLSX
    # without it, so the path is configuration rather than a built-in default.
    settings = load_settings(
        environ={
            "GRAPHME_VIEWER_DIR": str(tmp_path / "viewer"),
            "GRAPHME_RENDER_TIMEOUT_SECONDS": "45.5",
            "GRAPHME_CHROME_EXECUTABLE": "/opt/chrome",
        }
    )
    assert settings.viewer_directory == tmp_path / "viewer"
    assert settings.render_timeout_seconds == 45.5
    assert settings.chrome_executable == "/opt/chrome"


def test_viewer_directory_defaults_to_the_cache_location() -> None:
    assert load_settings(environ={}).viewer_directory.name == "ooxml-viewer"


def test_a_calibrated_model_gets_its_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    from graphme import config

    monkeypatch.setitem(config.RELEVANCE_FLOORS, "calibrated-test", 0.42)

    assert Settings(embedding_model="calibrated-test").relevance_floor == 0.42


def test_openai_large_embedder_keeps_passages_from_cosine_0_2() -> None:
    settings = Settings(embedding_model="text-embedding-3-large")

    assert settings.relevance_floor == 0.2


def test_an_uncalibrated_model_warns_and_keeps_the_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="graphme.config"):
        settings = Settings(embedding_model="uncalibrated-test")

    assert settings.relevance_floor == 0.3
    assert "GRAPHME_RELEVANCE_FLOOR" in caplog.text


def test_a_floor_set_by_hand_wins_and_says_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="graphme.config"):
        settings = load_settings(environ={"GRAPHME_EMBEDDING_MODEL": "uncalibrated-test",
                                          "GRAPHME_RELEVANCE_FLOOR": "0.5"})

    assert settings.relevance_floor == 0.5 and caplog.text == ""


def test_the_chosen_providers_key_is_read_from_its_own_variable() -> None:
    chat = model_endpoint.PROVIDERS[model_endpoint.DEFAULT_PROVIDER]

    settings = load_settings(environ={chat.key_env: "invented-key"})

    assert settings.api_key == "invented-key"


def test_a_plain_http_address_on_another_machine_is_refused() -> None:
    with pytest.raises(ValueError, match="GRAPHME_MODEL_BASE_URL|loopback"):
        Settings(base_url="http://example.com/v1")


def test_a_server_on_this_machine_is_accepted() -> None:
    assert Settings(base_url="http://127.0.0.1:8000/v1").base_url == "http://127.0.0.1:8000/v1"


def test_every_command_draws_with_the_viewer_the_installer_put_in_the_app_home(
    tmp_path: Path,
) -> None:
    # `graphme build-graph` after an install looked in the cache, found no viewer there, and
    # every DOCX, PPTX and XLSX failed to draw; only `graphme app` knew the home's viewer.
    home = tmp_path / "home"
    (home / "viewer" / "dist").mkdir(parents=True)

    assert load_settings({"GRAPHME_HOME": str(home)}).viewer_directory == home / "viewer"
    named = load_settings({"GRAPHME_HOME": str(home), "GRAPHME_VIEWER_DIR": str(tmp_path)})
    assert named.viewer_directory == tmp_path
    empty = tmp_path / "empty"
    empty.mkdir()
    assert load_settings({"GRAPHME_HOME": str(empty)}).viewer_directory != empty / "viewer"


def test_a_command_run_outside_a_checkout_uses_the_installed_app_s_settings_and_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `graphme build-graph` from a new terminal read only the working folder's `.env`: no key,
    # and the graph data in a `.graphme` of whatever folder the terminal was in.
    from graphme import model_endpoint

    provider = model_endpoint.DEFAULT_PROVIDER
    key_env = model_endpoint.PROVIDERS[provider].key_env
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(f"GRAPHME_PROVIDER={provider}\n{key_env}=invented\n",
                               encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("GRAPHME_HOME", str(home))
    monkeypatch.delenv(key_env, raising=False)
    monkeypatch.delenv("GRAPHME_DATA_DIR", raising=False)

    settings = load_settings()

    assert settings.api_key == "invented"
    assert settings.data_dir == home / ".graphme"

    (elsewhere / ".env").write_text(f"GRAPHME_PROVIDER={provider}\n", encoding="utf-8")
    assert load_settings().api_key is None  # a checkout's own `.env` is still its own


def test_settings_naming_no_neo4j_use_the_app_s_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("GRAPHME_PROFILE=test\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("GRAPHME_HOME", str(home))
    monkeypatch.delenv("GRAPHME_NEO4J_URI", raising=False)

    monkeypatch.chdir(elsewhere)  # a terminal anywhere: the app's settings, its own Neo4j
    assert load_settings().local_neo4j == home
    monkeypatch.chdir(home)  # the app itself, started in its home
    assert load_settings().local_neo4j == home

    (home / ".env").write_text("GRAPHME_NEO4J_URI=bolt://127.0.0.1:7999\n", encoding="utf-8")
    assert load_settings().local_neo4j is None  # one named on purpose
    (elsewhere / ".env").write_text("GRAPHME_PROFILE=test\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)  # a checkout's own .env naming none: the app's own too
    assert load_settings().local_neo4j == home
    assert load_settings({"GRAPHME_HOME": str(home)}).local_neo4j is None  # tests, explicit


def test_a_home_named_in_the_settings_file_is_where_its_neo4j_lives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A checkout keeps its graph apart from the installed app's by naming a home in its .env.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".env").write_text(f"GRAPHME_HOME={tmp_path / 'dev-home'}\n", encoding="utf-8")
    monkeypatch.chdir(checkout)
    monkeypatch.delenv("GRAPHME_HOME", raising=False)
    monkeypatch.delenv("GRAPHME_NEO4J_URI", raising=False)
    assert load_settings().local_neo4j == tmp_path / "dev-home"
