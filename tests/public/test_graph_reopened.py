"""A graph whose own Neo4j ended of itself is opened again on the next ask, not asked of a port
nothing answers on until the app is restarted. Nothing is started: the space is a stand-in."""
from __future__ import annotations

from pathlib import Path

import pytest

from graphme import api
from graphme.config import Settings


class _Space:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_a_graph_whose_neo4j_ended_is_opened_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[_Space] = []
    monkeypatch.setattr(api, "create_graph_space", lambda s: made.append(_Space()) or made[-1])
    ended: dict[str, bool] = {}
    # Says it once, as the real one does: it forgets the ended Neo4j as it tells.
    monkeypatch.setattr(api, "ended", lambda graph: ended.pop("now", False))
    graph = api.GraphMe(Settings(profile="test", data_dir=tmp_path / ".graphme",
                                 local_neo4j=tmp_path))

    first = graph.graph_space()
    assert graph.graph_space() is first and graph.opened_space is first
    ended["now"] = True  # its Neo4j ended of itself

    assert graph.opened_space is None  # a page's ask opens it again behind
    assert graph.opened_space is None  # and asked again, still not the ended one
    again = graph.graph_space()
    assert again is not first and made == [first, again] and first.closed


def test_the_answering_part_lets_go_of_a_graph_whose_neo4j_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spaces: list[_Space] = []
    monkeypatch.setattr(api, "create_graph_space", lambda s: spaces.append(_Space()) or spaces[-1])
    monkeypatch.setattr(api, "create_graph_rag", lambda settings, space, client: ("rag", space))
    ended: dict[str, bool] = {}
    # Says it once, as the real one does: it forgets the ended Neo4j as it tells.
    monkeypatch.setattr(api, "ended", lambda graph: ended.pop("now", False))
    graph = api.GraphMe(Settings(profile="test", data_dir=tmp_path / ".graphme",
                                 local_neo4j=tmp_path))

    first = graph.rag()
    ended["now"] = True

    assert graph.rag() != first and graph.rag()[1] is spaces[-1]  # type: ignore[index]
