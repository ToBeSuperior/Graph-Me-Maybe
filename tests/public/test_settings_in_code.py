"""Settings passed in code: the library's `read` and `GraphMe` take the common ones as keywords,
over what the environment says. Invented values only."""
from __future__ import annotations

from pathlib import Path

import pytest

import graphme
from graphme.config import load_settings


def test_keywords_win_over_the_environment(tmp_path: Path) -> None:
    environ = {"GRAPHME_HOME": str(tmp_path), "GRAPHME_PROFILE": "test",
               "GRAPHME_MODEL": "from-environment", "GRAPHME_NEO4J_USER": "env-user"}

    settings = load_settings(environ, model="from-code", neo4j_uri="bolt://127.0.0.1:7699",
                             neo4j_password="invented-secret")

    assert settings.model == "from-code"
    assert settings.neo4j_uri == "bolt://127.0.0.1:7699"
    assert settings.neo4j_user == "env-user"  # not passed: the environment's
    assert "invented-secret" not in repr(settings)


def test_a_key_passed_is_the_key_whatever_the_environment_holds(tmp_path: Path) -> None:
    environ = {"GRAPHME_HOME": str(tmp_path), "GRAPHME_PROFILE": "test"}

    settings = load_settings(environ, api_key="invented-chat-key",
                             embedding_key="invented-embedding-key")

    assert settings.api_key == "invented-chat-key"
    assert settings.embedding_key == "invented-embedding-key"


def test_home_names_where_the_graph_and_its_files_are(tmp_path: Path) -> None:
    settings = load_settings({"GRAPHME_PROFILE": "test", "HOME": "/nowhere",
                              "LOCALAPPDATA": "/nowhere"}, home=tmp_path)

    assert settings.local_neo4j == tmp_path or settings.local_models == tmp_path


def test_an_unknown_setting_is_named() -> None:
    with pytest.raises(TypeError, match="api_kye"):
        load_settings({}, api_kye="invented")


def test_settings_and_keywords_together_are_refused(tmp_path: Path) -> None:
    settings = load_settings({"GRAPHME_HOME": str(tmp_path), "GRAPHME_PROFILE": "test"})

    with pytest.raises(TypeError, match="settings"):
        graphme.GraphMe(settings, model="invented")
    with pytest.raises(TypeError, match="settings"):
        graphme.read(tmp_path / "n.pdf", settings=settings, model="invented")


def test_graphme_takes_keywords(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHME_PROFILE", "test")
    monkeypatch.setenv("GRAPHME_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)  # no checkout's .env

    space = graphme.GraphMe(model="invented-model", neo4j_uri="bolt://127.0.0.1:7698")

    assert space.settings.model == "invented-model"
    assert space.settings.neo4j_uri == "bolt://127.0.0.1:7698"
