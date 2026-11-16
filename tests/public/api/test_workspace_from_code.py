"""`GraphMe(workspace="name")` opens that workspace of the app's, making it when missing."""
from __future__ import annotations

from pathlib import Path

import pytest

from graphme import api
from graphme.config import load_settings
from graphme.server.workspaces import Workspaces


def test_a_workspace_is_opened_by_name_and_made_once(tmp_path: Path) -> None:
    settings = load_settings({"GRAPHME_PROFILE": "test"}).model_copy(
        update={"local_neo4j": tmp_path})

    first = api.GraphMe(settings, workspace="Invented audits")
    again = api.GraphMe(settings, workspace="Invented audits")

    registry = Workspaces(tmp_path)
    [made] = [i for i in registry.listed()["items"] if i["name"] == "Invented audits"]
    assert first.settings.workspace == again.settings.workspace == made["id"]
    assert first.settings.local_graph == registry.graph(made["id"])
    assert registry.active == "default"  # the app's open workspace is left as it was


def test_a_workspace_needs_the_app_s_own_neo4j() -> None:
    settings = load_settings({"GRAPHME_PROFILE": "test", "GRAPHME_NEO4J_URI": "bolt://invented:7687"})
    with pytest.raises(ValueError, match="workspace"):
        api.GraphMe(settings, workspace="Invented audits")
