"""A graph's crops are kept beside it: wherever the command runs, the crops and the graph they
belong to are the same place's, so a sweep of one graph never reads another's crops as unused.
Invented folders."""
from __future__ import annotations

from pathlib import Path

from graphme.config import Settings


def test_the_homes_graph_keeps_its_crops_in_the_home_wherever_it_is_run(tmp_path: Path) -> None:
    home, elsewhere = tmp_path / "home", tmp_path / "somewhere" / ".graphme"

    settings = Settings(profile="test", data_dir=elsewhere, local_neo4j=home)

    assert settings.asset_dir == home / ".graphme" / "assets"


def test_a_workspace_keeps_its_crops_beside_its_own_graph(tmp_path: Path) -> None:
    home, space = tmp_path / "home", tmp_path / "home" / "workspaces" / "a"

    settings = Settings(profile="test", data_dir=tmp_path / ".graphme", local_neo4j=home,
                        local_graph=space)

    assert settings.asset_dir == space / "assets"


def test_a_neo4j_of_its_own_leaves_the_crops_in_the_data_folder(tmp_path: Path) -> None:
    settings = Settings(profile="test", data_dir=tmp_path / ".graphme")

    assert settings.asset_dir == tmp_path / ".graphme" / "assets"
