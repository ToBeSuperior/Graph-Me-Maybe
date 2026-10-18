"""The installers and the release they install from agree on what the release holds."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ("web.zip", "viewer.zip", "SHA256SUMS")


def test_the_release_build_makes_what_both_installers_fetch() -> None:
    build = (ROOT / "tools/release/build.sh").read_text()
    for script in ("install.sh", "install.ps1"):
        text = (ROOT / script).read_text()
        for asset in ASSETS:
            assert asset in build and asset in text, (script, asset)
        assert "graph_me_maybe-" in text


posix = pytest.mark.skipif(sys.platform == "win32", reason="install.sh is the POSIX installer")


def _assets(folder: Path, *, bad: str | None = None) -> Path:
    folder.mkdir(parents=True)
    files = {"graph_me_maybe-0.0.0-py3-none-any.whl": b"wheel", "web.zip": b"web",
             "viewer.zip": b"viewer"}
    for name, data in files.items():
        (folder / name).write_bytes(data)
    sums = [f"{hashlib.sha256(b'other' if name == bad else data).hexdigest()}  {name}"
            for name, data in files.items()]
    (folder / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return folder


def _install(tmp_path: Path, source: Path) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "Application Support" / "Graph-Me-Maybe"
    env = {**_scratch_env(tmp_path), "GRAPHME_INSTALL_SOURCE": str(source),
           "GRAPHME_HOME": str(home),
           "GRAPHME_NO_DOCKER": "1", "GRAPHME_NO_SHORTCUT": "1", "GRAPHME_NO_LAUNCH": "1",
           "GRAPHME_YES": "1", "GRAPHME_VERSION": "v0.0.0"}
    return subprocess.run(["sh", str(ROOT / "install.sh")], env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=60)


@posix
def test_a_checksum_that_does_not_match_installs_nothing(tmp_path: Path) -> None:
    done = _install(tmp_path, _assets(tmp_path / "assets", bad="web.zip"))
    assert done.returncode != 0
    assert "web.zip" in done.stderr and "checksum" in done.stderr
    assert not (tmp_path / "Application Support" / "Graph-Me-Maybe" / "web").exists()


@posix
def test_a_missing_asset_installs_nothing(tmp_path: Path) -> None:
    source = _assets(tmp_path / "assets")
    (source / "viewer.zip").unlink()
    done = _install(tmp_path, source)
    assert done.returncode != 0 and "viewer.zip" in done.stderr


@posix
def test_a_question_without_a_terminal_is_no_and_does_not_wait(tmp_path: Path) -> None:
    script = (f'GRAPHME_INSTALL_LIB=1 . "{ROOT / "install.sh"}";'
              ' command -v ask >/dev/null || exit 3;'
              ' if ask "Install?"; then echo yes; else echo no; fi')
    done = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=10,
                          env={k: v for k, v in os.environ.items() if k != "GRAPHME_YES"}
                          | {"GRAPHME_NO_TTY": "1"})
    assert done.returncode == 0 and done.stdout.strip() == "no"


@posix
def test_the_script_parses() -> None:
    assert subprocess.run(["sh", "-n", str(ROOT / "install.sh")]).returncode == 0


def _uninstall_env(tmp_path: Path, graphme_home: str) -> dict[str, str]:
    assert shutil.which("uv"), "uv must be on PATH so the script never downloads it"
    return {**os.environ, "HOME": str(tmp_path / "home"), "UV_TOOL_DIR": str(tmp_path / "tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / "bin"), "UV_NO_MODIFY_PATH": "1",
            "GRAPHME_PURGE": "1", "GRAPHME_HOME": graphme_home}


@posix
@pytest.mark.parametrize("graphme_home", ["HOME", "relative"])
def test_a_purge_refuses_home_or_a_relative_folder(tmp_path: Path, graphme_home: str) -> None:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "marker").write_text("keep")
    (home / "data" / "marker").write_text("keep")
    target = str(home) if graphme_home == "HOME" else "data"
    done = subprocess.run(["sh", str(ROOT / "install.sh"), "--uninstall"],
                          env=_uninstall_env(tmp_path, target), cwd=home, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert done.returncode != 0 and "GRAPHME_HOME" in done.stderr
    assert (home / "marker").exists() and (home / "data" / "marker").exists()


@posix
def test_the_shortcut_opens_the_installed_home(tmp_path: Path) -> None:
    custom = tmp_path / "My Data" / "Graph-Me-Maybe"
    script = (f'GRAPHME_INSTALL_LIB=1 . "{ROOT / "install.sh"}"; uname() {{ echo Darwin; }};'
              f' HOME_DIR="{custom}"; make_shortcut "{tmp_path / "bin"}"')
    env = {k: v for k, v in os.environ.items() if k != "GRAPHME_NO_SHORTCUT"}
    done = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=10,
                          env=env | {"HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr
    launcher = tmp_path / "Applications/Graph-Me-Maybe.app/Contents/MacOS/Graph-Me-Maybe"
    assert f'GRAPHME_HOME="{custom}"' in launcher.read_text()
    assert subprocess.run(["sh", "-n", str(launcher)]).returncode == 0


def test_the_windows_installer_checks_before_it_installs_and_can_be_removed() -> None:
    text = (ROOT / "install.ps1").read_text()
    assert text.index("Get-FileHash") < text.index("uv tool install")
    for needed in ("--only-shell", "Docker.DockerDesktop", "--accept-package-agreements",
                   r"CurrentVersion\Uninstall\Graph-Me-Maybe", "CreateShortcut",
                   "GRAPHME_UNINSTALL", "GRAPHME_PURGE"):
        assert needed in text, needed


def test_the_windows_purge_is_guarded_before_anything_runs() -> None:
    text = (ROOT / "install.ps1").read_text()
    uninstall = text[text.index("function Uninstall"):text.index("function Install")]
    guard = uninstall.index("GRAPHME_PURGE")
    assert guard < uninstall.index("Ensure-Uv")
    for needed in ("IsPathRooted", "USERPROFILE", "Fail"):
        assert needed in uninstall[guard:uninstall.index("Ensure-Uv")], needed


def test_the_windows_installer_leaves_path_alone_when_asked() -> None:
    text = (ROOT / "install.ps1").read_text()
    assert "UV_NO_MODIFY_PATH" in text[:text.index("$ErrorActionPreference")]
    line = next(x for x in text.splitlines() if "uv tool update-shell" in x)
    assert "UV_NO_MODIFY_PATH" in line


def test_the_windows_shortcut_carries_a_custom_home() -> None:
    text = (ROOT / "install.ps1").read_text()
    shortcut = text[text.index("function Make-Shortcut"):text.index("function Register-Uninstall")]
    assert "$env:ComSpec" in shortcut and "/c set `\"GRAPHME_HOME=$AppHome`\"" in shortcut


@posix
def test_a_purge_refuses_a_folder_that_holds_home(tmp_path: Path) -> None:
    home = tmp_path / "users" / "me"
    home.mkdir(parents=True)
    (home / "marker").write_text("keep")
    env = _uninstall_env(tmp_path, str(tmp_path / "users")) | {"HOME": str(home)}
    done = subprocess.run(["sh", str(ROOT / "install.sh"), "--uninstall"], env=env, cwd=home,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert done.returncode != 0 and "GRAPHME_HOME" in done.stderr
    assert (home / "marker").exists()


def _windows_uninstall() -> str:
    text = (ROOT / "install.ps1").read_text()
    return text[text.index("function Uninstall"):text.index("function Install")]


def test_the_windows_purge_refuses_what_holds_the_profile_or_app_data() -> None:
    uninstall = _windows_uninstall()
    guard = uninstall[uninstall.index("GRAPHME_PURGE"):uninstall.index("Ensure-Uv")]
    for needed in ("StartsWith", "OrdinalIgnoreCase", "$env:LOCALAPPDATA", "$env:APPDATA"):
        assert needed in guard, needed


def test_the_windows_uninstall_stops_while_the_app_still_runs() -> None:
    uninstall = _windows_uninstall()
    check = uninstall.index("graphme.exe")
    assert uninstall.index("uv tool uninstall") < check < uninstall.index("Remove-Item")
    assert "close Graph-Me-Maybe first" in uninstall[check:uninstall.index("Remove-Item")]


def _scratch_env(tmp_path: Path) -> dict[str, str]:
    assert shutil.which("uv"), "uv must be on PATH so the script never downloads it"
    return {**os.environ, "HOME": str(tmp_path / "home"), "UV_TOOL_DIR": str(tmp_path / "tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / "bin"), "UV_NO_MODIFY_PATH": "1"}


@posix
def test_the_mac_shortcut_finds_docker_where_finder_does_not_look(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "graphme").write_text('#!/bin/sh\necho "PATH=$PATH"\n')
    (bin_dir / "graphme").chmod(0o755)
    data = tmp_path / "data"
    data.mkdir()
    script = (f'GRAPHME_INSTALL_LIB=1 . "{ROOT / "install.sh"}"; uname() {{ echo Darwin; }};'
              f' HOME_DIR="{data}"; make_shortcut "{bin_dir}"')
    env = {k: v for k, v in os.environ.items() if k != "GRAPHME_NO_SHORTCUT"}
    done = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=10,
                          env=env | {"HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr
    launcher = tmp_path / "Applications/Graph-Me-Maybe.app/Contents/MacOS/Graph-Me-Maybe"
    assert subprocess.run(["sh", "-n", str(launcher)]).returncode == 0
    subprocess.run([str(launcher)], env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
                   timeout=10, check=True)
    path = (data / "app.log").read_text().strip().removeprefix("PATH=").split(":")
    for folder in ("/usr/local/bin", "/opt/homebrew/bin", f"{tmp_path}/.docker/bin"):
        assert folder in path, folder


APP_FILES = (".env", ".graphme/graph.sqlite3", "web/index.html", "viewer/dist/x.js", "app.log",
             "install.ps1")


def _app_home(tmp_path: Path, *, mine: bool) -> Path:
    data = tmp_path / "scratch data"
    for name in APP_FILES:
        (data / name).parent.mkdir(parents=True, exist_ok=True)
        (data / name).write_text("app")
    if mine:
        (data / "mine.txt").write_text("keep")
    return data


@posix
@pytest.mark.parametrize("mine", [True, False])
def test_a_purge_removes_only_what_the_app_made(tmp_path: Path, mine: bool) -> None:
    data = _app_home(tmp_path, mine=mine)
    (tmp_path / "home").mkdir()
    env = _scratch_env(tmp_path) | {"GRAPHME_PURGE": "1", "GRAPHME_HOME": str(data)}
    done = subprocess.run(["sh", str(ROOT / "install.sh"), "--uninstall"], env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert done.returncode == 0, done.stderr
    if mine:
        assert sorted(p.name for p in data.iterdir()) == ["mine.txt"]
    else:
        assert not data.exists()


def test_the_windows_purge_removes_only_what_the_app_made() -> None:
    uninstall = _windows_uninstall()
    purge = uninstall[uninstall.index("Remove-Item $UninstallKey"):]
    for name in (".env", ".graphme", "web", "viewer", "app.log", "install.ps1"):
        assert f"'{name}'" in uninstall, name
    assert "-Recurse -Force -ErrorAction SilentlyContinue; Say" not in purge
    assert "GetFileSystemInfos" in purge


@posix
def test_an_install_stops_while_the_app_runs(tmp_path: Path) -> None:
    import http.server
    import threading

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- the base's name
            body = b'{"status":"ok"}'
            self.send_response(200 if self.path == "/health" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        data = tmp_path / "data"
        env = _scratch_env(tmp_path) | {
            "GRAPHME_INSTALL_SOURCE": str(_assets(tmp_path / "assets")), "GRAPHME_HOME": str(data),
            "GRAPHME_NO_DOCKER": "1", "GRAPHME_NO_SHORTCUT": "1", "GRAPHME_NO_LAUNCH": "1",
            "GRAPHME_VERSION": "v0.0.0", "GRAPHME_PORT": str(server.server_address[1])}
        done = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=60)
        server.shutdown()
    assert done.returncode != 0 and "quit it first" in done.stderr, done.stderr
    assert not data.exists() and not (tmp_path / "tools").exists()


def test_the_windows_install_stops_while_the_app_runs() -> None:
    text = (ROOT / "install.ps1").read_text()
    install = text[text.index("function Install"):]
    assert install.index("Verify $name") < install.index("/health") < install.index(
        "uv tool install")
    assert "GRAPHME_PORT" in install and "quit it first" in install
    assert "close Graph-Me-Maybe if it is running" in install


@posix
def test_a_failed_browser_download_is_a_warning_not_silence() -> None:
    sh = next(x for x in (ROOT / "install.sh").read_text().splitlines()
              if "playwright install" in x)
    assert "|| say" in sh and "warning" in sh
    ps = (ROOT / "install.ps1").read_text()
    for command in ("playwright install", "winget install"):
        after = ps[ps.index(command):]
        check = after[:after.index("\n", after.index("\n") + 1)]
        assert "$LASTEXITCODE -ne 0" in check and "warning" in check, command


@posix
@pytest.mark.parametrize("custom", [True, False])
def test_the_closing_line_names_a_custom_home(tmp_path: Path, custom: bool) -> None:
    script = f'GRAPHME_INSTALL_LIB=1 . "{ROOT / "install.sh"}"; HOME_DIR=$(app_home); open_hint'
    env = {k: v for k, v in os.environ.items() if k != "GRAPHME_HOME"} | {"HOME": str(tmp_path)}
    if custom:
        env["GRAPHME_HOME"] = str(tmp_path / "My Data")
    done = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=10,
                          env=env)
    assert done.returncode == 0, done.stderr
    want = f'GRAPHME_HOME="{tmp_path / "My Data"}" graphme app' if custom else "graphme app"
    assert done.stdout.strip() == want


def test_the_windows_closing_line_names_a_custom_home_and_docker_needs_a_restart() -> None:
    text = (ROOT / "install.ps1").read_text()
    assert "$env:GRAPHME_HOME='" in text[text.index("function Install"):]
    assert "the app waits for it" not in text and "restart" in text


@posix
@pytest.mark.parametrize(("fonts", "warned"),
                         [("", True), ("NotoSansCJK-Regular.ttc: Noto", False)])
def test_linux_without_a_hangul_font_is_told_how_to_get_one(fonts: str, warned: bool) -> None:
    # Chrome there sees only Linux's fonts (under WSL, not Windows'): Korean pages draw as boxes.
    script = (f'GRAPHME_INSTALL_LIB=1 . "{ROOT / "install.sh"}"; uname() {{ echo Linux; }};'
              f' fc_list() {{ printf "%s" "{fonts}"; }}; check_hangul_font')
    done = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=10)
    assert done.returncode == 0, done.stderr
    assert ("fonts-noto-cjk" in done.stdout + done.stderr) is warned
