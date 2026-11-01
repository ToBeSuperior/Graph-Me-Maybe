"""Where the running app is found: `server.json` in its home, the user's alone, gone with it."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from graphme import agent_file


def test_the_file_says_where_the_agent_answers_and_only_its_user_reads_it(tmp_path: Path) -> None:
    written = agent_file.write_server_file(tmp_path, port=8765, token="invented-token")

    read = agent_file.read_server_file(tmp_path)

    assert read == written and read.port == 8765 and read.pid == os.getpid()
    assert read.token == "invented-token" and read.window_pid is None
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "server.json").stat().st_mode) == 0o600


def test_a_file_left_by_an_agent_that_died_reads_as_none(tmp_path: Path) -> None:
    agent_file.write_server_file(tmp_path, port=8765, token="t")

    assert agent_file.read_server_file(tmp_path, alive=lambda pid: False) is None


@pytest.mark.parametrize("text", ["", "not json", '{"port": "x"}', "[]"])
def test_a_file_that_cannot_be_read_reads_as_none(tmp_path: Path, text: str) -> None:
    (tmp_path / "server.json").write_text(text, encoding="utf-8")

    assert agent_file.read_server_file(tmp_path) is None


def test_the_window_says_it_is_open_and_the_agent_s_file_keeps_the_rest(tmp_path: Path) -> None:
    agent_file.write_server_file(tmp_path, port=8765, token="t")

    agent_file.set_window_pid(tmp_path, 4321)

    read = agent_file.read_server_file(tmp_path)
    assert read is not None and read.window_pid == 4321 and read.port == 8765


def test_an_agent_removes_its_own_file_and_leaves_another_s(tmp_path: Path) -> None:
    agent_file.write_server_file(tmp_path, port=8765, token="t")
    text = (tmp_path / "server.json").read_text(encoding="utf-8")
    (tmp_path / "server.json").write_text(text.replace(str(os.getpid()), "1"), encoding="utf-8")

    agent_file.remove_server_file(tmp_path)
    assert (tmp_path / "server.json").exists()  # another agent's

    agent_file.write_server_file(tmp_path, port=8765, token="t")
    agent_file.remove_server_file(tmp_path)
    assert not (tmp_path / "server.json").exists()


def test_this_process_is_alive_and_a_made_up_pid_is_not() -> None:
    assert agent_file.pid_alive(os.getpid())
    assert not agent_file.pid_alive(2**22 + 12345)
