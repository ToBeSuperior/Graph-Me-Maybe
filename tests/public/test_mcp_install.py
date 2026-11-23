from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphme import mcp_install

ENTRY = {"command": "/opt/gmm/bin/graphme", "args": ["mcp"]}


def _where(tmp_path: Path, client: str, platform: str = "darwin") -> Path:
    return mcp_install.config_path(client, user_home=tmp_path, platform=platform,
                                   environ={"APPDATA": str(tmp_path / "AppData")})


def test_each_client_has_its_own_file_per_system(tmp_path: Path) -> None:
    assert _where(tmp_path, "claude-code") == tmp_path / ".claude.json"
    assert _where(tmp_path, "cursor") == tmp_path / ".cursor" / "mcp.json"
    assert _where(tmp_path, "claude-desktop") == (
        tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    assert _where(tmp_path, "claude-desktop", "win32") == (
        tmp_path / "AppData" / "Claude" / "claude_desktop_config.json")
    assert _where(tmp_path, "claude-desktop", "linux") == (
        tmp_path / ".config" / "Claude" / "claude_desktop_config.json")


def test_install_keeps_every_other_key_and_backs_the_file_up(tmp_path: Path) -> None:
    path = _where(tmp_path, "claude-code")
    before = {"numStartups": 7, "projects": {"/x": {"a": [1, 2]}},
              "mcpServers": {"other": {"command": "x", "args": []}}}
    path.write_text(json.dumps(before), encoding="utf-8")

    written = mcp_install.install("claude-code", user_home=tmp_path, platform="darwin", entry=ENTRY)

    assert written == path
    assert json.loads(path.read_text(encoding="utf-8")) == {
        **before, "mcpServers": {"other": {"command": "x", "args": []}, "graph-me-maybe": ENTRY}}
    assert json.loads(path.with_name(".claude.json.bak").read_text(encoding="utf-8")) == before


def test_install_twice_leaves_one_entry(tmp_path: Path) -> None:
    for _ in range(2):
        mcp_install.install("cursor", user_home=tmp_path, platform="darwin", entry=ENTRY)

    said = json.loads(_where(tmp_path, "cursor").read_text(encoding="utf-8"))
    assert said == {"mcpServers": {"graph-me-maybe": ENTRY}}
    assert mcp_install.registered(_where(tmp_path, "cursor"))


def test_install_makes_a_missing_file_and_folder(tmp_path: Path) -> None:
    path = mcp_install.install("claude-desktop", user_home=tmp_path, platform="darwin", entry=ENTRY)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "mcpServers": {"graph-me-maybe": ENTRY}}
    assert not path.with_name(path.name + ".bak").exists()


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '{"mcpServers": []}'])
def test_install_refuses_a_file_it_cannot_keep_whole(tmp_path: Path, text: str) -> None:
    path = _where(tmp_path, "cursor")
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")

    with pytest.raises(mcp_install.ConfigError, match="mcp.json"):
        mcp_install.install("cursor", user_home=tmp_path, platform="darwin", entry=ENTRY)

    assert path.read_text(encoding="utf-8") == text
    assert not path.with_name("mcp.json.bak").exists()


def test_install_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    mcp_install.install("claude-code", user_home=tmp_path, platform="darwin", entry=ENTRY)

    assert sorted(p.name for p in tmp_path.iterdir()) == [".claude.json"]


def test_the_entry_names_the_running_script_by_absolute_path(tmp_path: Path) -> None:
    script = tmp_path / "bin" / "graphme"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")

    by_argv = mcp_install.current_entry(argv0=str(script), which=lambda name: None)
    by_path = mcp_install.current_entry(argv0="/x/python", which=lambda name: str(script))

    assert by_argv == by_path == {"command": str(script), "args": ["mcp"]}


def test_without_a_stable_script_the_entry_runs_this_python(tmp_path: Path) -> None:
    temporary = tmp_path / "uv" / "archive-v0" / "abc" / "bin" / "graphme"
    temporary.parent.mkdir(parents=True)
    temporary.write_text("", encoding="utf-8")
    want = {"command": "/py/bin/python", "args": ["-m", "graphme.cli", "mcp"]}

    assert mcp_install.current_entry(argv0="-c", which=lambda n: str(temporary),
                                     executable="/py/bin/python") == want
    assert mcp_install.current_entry(argv0="-c", which=lambda n: None,
                                     executable="/py/bin/python") == want


def test_the_fallback_entry_starts() -> None:
    import subprocess
    import sys

    entry = mcp_install.current_entry(argv0="-c", which=lambda n: None, executable=sys.executable)

    done = subprocess.run([entry["command"], *entry["args"][:-1], "--help"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "mcp" in done.stdout


def test_nothing_stable_to_point_at_is_refused(tmp_path: Path) -> None:
    gone = tmp_path / "uv" / "archive-v0" / "x" / "bin"

    with pytest.raises(mcp_install.ConfigError, match="uv tool install graph-me-maybe"):
        mcp_install.current_entry(argv0=str(gone / "graphme"),
                                  which=lambda n: str(gone / "graphme"),
                                  executable=str(gone / "python"))


def test_a_stable_graphme_on_the_path_beats_an_unstable_argv0(tmp_path: Path) -> None:
    unstable = tmp_path / "uv" / "archive-v0" / "x" / "graphme"
    unstable.parent.mkdir(parents=True)
    unstable.write_text("", encoding="utf-8")
    stable = tmp_path / "bin" / "graphme"
    stable.parent.mkdir()
    stable.write_text("", encoding="utf-8")

    entry = mcp_install.current_entry(argv0=str(unstable), which=lambda n: str(stable))

    assert entry == {"command": str(stable), "args": ["mcp"]}


def test_a_stable_link_is_kept_not_resolved(tmp_path: Path) -> None:
    real = tmp_path / "Cellar" / "1.0" / "graphme"
    real.parent.mkdir(parents=True)
    real.write_text("", encoding="utf-8")
    link = tmp_path / "bin" / "graphme"
    link.parent.mkdir()
    link.symlink_to(real)

    assert mcp_install.current_entry(argv0="-c", which=lambda n: str(link))["command"] == str(link)


def test_a_stale_entry_is_rewritten_and_keeps_the_users_other_fields(tmp_path: Path) -> None:
    path = _where(tmp_path, "cursor")
    path.parent.mkdir(parents=True)
    old = {"mcpServers": {"graph-me-maybe": {"command": "graphme", "args": ["mcp"],
                                             "env": {"A": "1"}}}}
    path.write_text(json.dumps(old), encoding="utf-8")

    mcp_install.install("cursor", user_home=tmp_path, platform="darwin", entry=ENTRY)

    said = json.loads(path.read_text(encoding="utf-8"))
    assert said["mcpServers"]["graph-me-maybe"] == {**ENTRY, "env": {"A": "1"}}
    assert json.loads(path.with_name("mcp.json.bak").read_text(encoding="utf-8")) == old


def test_the_first_backup_is_kept(tmp_path: Path) -> None:
    path = _where(tmp_path, "cursor")
    path.parent.mkdir(parents=True)
    path.write_text('{"first": 1}', encoding="utf-8")
    mcp_install.install("cursor", user_home=tmp_path, platform="darwin", entry=ENTRY)
    mcp_install.install("cursor", user_home=tmp_path, platform="darwin",
                        entry={"command": "/other/graphme", "args": ["mcp"]})

    assert json.loads(path.with_name("mcp.json.bak").read_text(encoding="utf-8")) == {"first": 1}


def test_a_symlinked_config_stays_a_link_and_its_target_changes(tmp_path: Path) -> None:
    target = tmp_path / "dotfiles" / "mcp.json"
    target.parent.mkdir()
    target.write_text("{}", encoding="utf-8")
    link = _where(tmp_path, "cursor")
    link.parent.mkdir(parents=True)
    link.symlink_to(target)

    mcp_install.install("cursor", user_home=tmp_path, platform="darwin", entry=ENTRY)

    assert link.is_symlink()
    assert mcp_install.registered(target)


def test_a_file_with_a_byte_order_mark_is_read(tmp_path: Path) -> None:
    path = _where(tmp_path, "claude-desktop")
    path.parent.mkdir(parents=True)
    path.write_text('\ufeff{"keep": true}', encoding="utf-8")

    mcp_install.install("claude-desktop", user_home=tmp_path, platform="darwin", entry=ENTRY)

    assert json.loads(path.read_text(encoding="utf-8"))["keep"] is True


def test_an_old_url_entry_is_replaced_by_the_stdio_shape_keeping_only_env(tmp_path: Path) -> None:
    path = _where(tmp_path, "cursor")
    path.parent.mkdir(parents=True)
    old = {"type": "http", "url": "http://127.0.0.1:1/mcp", "headers": {"A": "b"},
           "env": {"K": "v"}, "command": ENTRY["command"], "args": ["mcp"]}
    path.write_text(json.dumps({"mcpServers": {"graph-me-maybe": old}}), encoding="utf-8")

    mcp_install.install("cursor", user_home=tmp_path, platform="darwin", entry=ENTRY)

    assert json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["graph-me-maybe"] == {
        **ENTRY, "env": {"K": "v"}}


def test_a_matching_entry_whose_program_is_gone_is_written_again(tmp_path: Path) -> None:
    gone = {"command": str(tmp_path / "gone" / "graphme"), "args": ["mcp"]}
    here = tmp_path / "graphme"
    here.write_text("#!/bin/sh\n", encoding="utf-8")
    here.chmod(0o755)
    path = _where(tmp_path, "cursor")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"graph-me-maybe": gone}}), encoding="utf-8")

    assert not mcp_install.installed(gone, gone)
    mcp_install.install("cursor", user_home=tmp_path, platform="darwin",
                        entry={"command": str(here), "args": ["mcp"]})

    assert json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["graph-me-maybe"] == {
        "command": str(here), "args": ["mcp"]}
    assert mcp_install.installed({"command": str(here), "args": ["mcp"]},
                                 {"command": str(here), "args": ["mcp"]})
    assert not mcp_install.installed({**ENTRY, "url": "http://x"}, ENTRY)


def test_the_project_scopes_of_claude_code_are_listed(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps({"projects": {"/p/one": {"mcpServers": {
        "graph-me-maybe": ENTRY, "x": {}}}, "/p/two": {}, "/p/odd": 3}}), encoding="utf-8")

    assert mcp_install.registered_scopes(path) == [("project /p/one", ENTRY)]
