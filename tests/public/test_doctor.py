from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from graphme import agent_file, doctor, mcp_install
from graphme.config import load_settings

_ENTRY = {"command": sys.executable, "args": ["-m", "graphme", "mcp"]}


def _settings(home: Path, update: dict | None = None, **env: str):
    settings = load_settings(environ={"GRAPHME_HOME": str(home), **env})
    update = update or {}
    # As the CLI's settings have it, and whatever keys this machine's environment holds.
    return settings.model_copy(update={"local_neo4j": home, "api_key": None,
                                       "embedding_key": None, **update})


def _by_name(found: list[doctor.Check]) -> dict[str, doctor.Check]:
    return {check.name: check for check in found}


def _run(tmp_path: Path, **env: str) -> dict[str, doctor.Check]:
    home = tmp_path / "home"
    settings = _settings(home, GRAPHME_VIEWER_DIR=str(tmp_path / "no-viewer"), **env)
    return _by_name(doctor.checks(home, settings, user_home=tmp_path, platform="darwin",
                                  environ={"HOME": str(tmp_path)}))


def test_an_empty_machine_fails_each_check_with_one_fix(tmp_path: Path) -> None:
    found = _run(tmp_path)

    assert set(found) >= {"app", "graph database", "search model", "model key", "viewer",
                          "browser", "MCP in Claude Code", "MCP in Claude Desktop",
                          "MCP in Cursor"}
    for name in ("graph database", "search model", "viewer"):
        assert not found[name].ok
        assert found[name].problem and found[name].fix
    assert all(found[f"MCP in {c}"].ok for c in ("Claude Code", "Claude Desktop", "Cursor")), (
        "a client that is not installed has nothing to register with")
    assert found["app"].ok and "not running" in found["app"].note, "the app starts on demand"
    assert all(c.fix != "graphme app" for c in found.values())
    cursor = found["MCP in Cursor"]
    assert cursor.note == (f"not found here (looked in {tmp_path / '.cursor' / 'mcp.json'}); "
                           "to register anyway: graphme mcp install --client cursor")
    assert "graphme setup" in found["graph database"].fix


def test_a_missing_model_key_is_a_problem_pointing_at_the_keys_page(
        tmp_path: Path, monkeypatch) -> None:
    from graphme import model_endpoint

    monkeypatch.setattr(model_endpoint, "KEY_REQUIRED", True)
    found = _run(tmp_path)

    assert not found["model key"].ok
    assert "Manage › Keys and model" in found["model key"].fix


def test_what_the_app_would_find_is_what_the_doctor_reports(tmp_path: Path) -> None:
    home = tmp_path / "home"
    viewer = tmp_path / "viewer"
    (viewer / "dist").mkdir(parents=True)
    chrome = tmp_path / "chrome"
    chrome.write_text("", encoding="utf-8")
    settings = _settings(home, GRAPHME_VIEWER_DIR=str(viewer),
                         GRAPHME_CHROME_EXECUTABLE=str(chrome))
    agent_file.write_server_file(home, port=8123, token="secret-token")

    found = _by_name(doctor.checks(
        home, settings, user_home=tmp_path, platform="darwin", environ={},
        answers=lambda said: said.port == 8123))

    assert found["app"].ok and found["viewer"].ok and found["browser"].ok
    assert "secret-token" not in json.dumps([vars(c) for c in found.values()])


def test_an_app_that_does_not_answer_is_not_ok(tmp_path: Path) -> None:
    home = tmp_path / "home"
    agent_file.write_server_file(home, port=8123, token="t")

    found = _by_name(doctor.checks(home, _settings(home), user_home=tmp_path,
                                   platform="darwin", environ={}, answers=lambda said: False))

    assert not found["app"].ok


def test_a_registered_client_passes_and_nothing_is_written(tmp_path: Path) -> None:
    mcp_install.install("cursor", user_home=tmp_path, platform="darwin", entry=_ENTRY)
    (tmp_path / ".claude.json").write_text("{}", encoding="utf-8")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    found = _run(tmp_path)

    assert found["MCP in Cursor"].ok
    assert not found["MCP in Claude Code"].ok
    assert "graphme mcp install --client claude-code" in found["MCP in Claude Code"].fix
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")
                   if "home" not in p.parts)
    assert after == [p for p in before if not p.startswith("home")]


def test_a_config_that_is_not_json_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / ".claude.json").write_text("{oops", encoding="utf-8")

    found = _run(tmp_path)

    assert not found["MCP in Claude Code"].ok
    assert "not valid JSON" in found["MCP in Claude Code"].problem


def test_the_cli_prints_json_and_exits_nonzero_on_a_problem(tmp_path: Path, monkeypatch) -> None:
    from graphme.cli import app

    monkeypatch.setenv("GRAPHME_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    invocation = CliRunner().invoke(app, ["doctor", "--json"])

    said = json.loads(invocation.stdout)
    assert invocation.exit_code == 1
    assert {"name", "ok", "problem", "fix", "note"} == set(said[0])


def test_the_cli_installs_for_a_client(tmp_path: Path, monkeypatch) -> None:
    from graphme.cli import app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text('{"keep": 1}', encoding="utf-8")

    invocation = CliRunner().invoke(app, ["mcp", "install", "--client", "cursor"])

    assert invocation.exit_code == 0, invocation.stdout
    assert mcp_install.registered(tmp_path / ".cursor" / "mcp.json")
    path = tmp_path / ".cursor" / "mcp.json"
    backup = path.with_name("mcp.json.bak")
    before = (path.read_bytes(), backup.read_bytes())
    again = CliRunner().invoke(app, ["mcp", "install", "--client", "cursor"])
    assert again.exit_code == 0
    assert (path.read_bytes(), backup.read_bytes()) == before
    bad = CliRunner().invoke(app, ["mcp", "install", "--client", "emacs"])
    assert bad.exit_code != 0


def test_the_setup_skill_names_what_the_commands_are() -> None:
    skill = Path(__file__).resolve().parents[2] / "skills" / "graph-me-maybe" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")

    assert text.startswith("---\nname: graph-me-maybe\ndescription: ")
    for said in ("graphme doctor --json", "graphme mcp install --client", "list_workspaces"):
        assert said in text


def test_a_registered_command_that_is_gone_fails_with_the_install_as_fix(tmp_path: Path) -> None:
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"graph-me-maybe": {
        "command": str(tmp_path / "gone" / "graphme"), "args": ["mcp"]}}}), encoding="utf-8")

    found = _run(tmp_path)["MCP in Claude Code"]

    assert not found.ok
    assert found.problem == f"registered command {tmp_path / 'gone' / 'graphme'} does not exist"
    assert found.fix == "graphme mcp install --client claude-code"


def test_the_cli_says_so_when_install_meets_a_corrupt_file(tmp_path: Path, monkeypatch) -> None:
    from graphme.cli import app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / ".claude.json").write_text("{oops", encoding="utf-8")

    invocation = CliRunner().invoke(app, ["mcp", "install", "--client", "claude-code"])

    assert invocation.exit_code == 1
    assert "not valid JSON" in invocation.output
    assert (tmp_path / ".claude.json").read_text(encoding="utf-8") == "{oops"


def test_a_claude_folder_counts_as_claude_code_being_here(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()

    found = _run(tmp_path)["MCP in Claude Code"]

    assert not found.ok and not found.note


def test_a_server_entry_without_a_command_is_not_a_missing_command(tmp_path: Path) -> None:
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"graph-me-maybe": {
        "type": "http", "url": "http://localhost:1/mcp"}}}), encoding="utf-8")

    found = _run(tmp_path)["MCP in Claude Code"]

    assert found.ok and found.note == "registered (not a command entry)"


def test_the_cli_prints_a_note_in_text_mode(tmp_path: Path, monkeypatch) -> None:
    from graphme.cli import app

    monkeypatch.setenv("GRAPHME_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    out = CliRunner().invoke(app, ["doctor"]).stdout

    assert "     note: not running; `graphme mcp` starts it when a client asks" in out
    assert "note: not found here (looked in" in out


def test_an_app_that_does_not_answer_says_to_quit_and_reopen(tmp_path: Path) -> None:
    home = tmp_path / "home"
    agent_file.write_server_file(home, port=8123, token="t")

    found = _by_name(doctor.checks(home, _settings(home), user_home=tmp_path,
                                   platform="darwin", environ={}, answers=lambda said: False))

    assert found["app"].fix == ("quit the app from its menu bar or tray icon (Quit completely), "
                                "then open it again")


def test_a_claude_code_entry_in_a_project_scope_counts_and_names_the_scope(
        tmp_path: Path) -> None:
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {"/work/x": {"mcpServers": {
        "graph-me-maybe": {"command": sys.executable, "args": ["mcp"]}}}}}), encoding="utf-8")

    found = _run(tmp_path)["MCP in Claude Code"]

    assert found.ok and "project /work/x" in found.note


def test_an_invalid_dotenv_is_a_fail_row_with_its_fix_not_a_crash(
        tmp_path: Path, monkeypatch) -> None:
    from graphme.cli import app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GRAPHME_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("this is not an entry\n", encoding="utf-8")

    out = CliRunner().invoke(app, ["doctor"])

    assert out.exit_code == 1
    assert "FAIL settings" in out.stdout and ".env" in out.stdout and "fix:" in out.stdout
