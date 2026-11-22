"""The right-click "Ask Graph-Me-Maybe": a Finder Quick Action on macOS, an Explorer verb on
Windows, for this user only."""
from __future__ import annotations

import plistlib
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from graphme import context_menu

COMMAND = ["/Invented Apps/python3", "-m", "graphme.cli", "ask-window", "--home", "/invented/home"]


def test_on_macos_it_is_a_quick_action_running_the_command_on_the_picked_files(
    tmp_path: Path,
) -> None:
    context_menu.install(COMMAND, platform="darwin", folder=tmp_path)

    bundle = tmp_path / "Ask Graph-Me-Maybe.workflow" / "Contents"
    info = plistlib.loads((bundle / "Info.plist").read_bytes())
    service = info["NSServices"][0]
    assert service["NSMenuItem"]["default"] == "Ask Graph-Me-Maybe"
    assert service["NSMessage"] == "runWorkflowAsService"
    assert service["NSSendFileTypes"] == ["public.item"]  # files and folders
    assert service["NSRequiredContext"] == {"NSApplicationIdentifier": "com.apple.finder"}
    flow = plistlib.loads((bundle / "document.wflow").read_bytes())
    assert flow["workflowMetaData"]["workflowTypeIdentifier"] == "com.apple.Automator.servicesMenu"
    assert flow["workflowMetaData"]["serviceInputTypeIdentifier"] == \
        "com.apple.Automator.fileSystemObject"
    [step] = flow["actions"]
    parameters = step["action"]["ActionParameters"]
    assert parameters["inputMethod"] == 1  # the picked paths as arguments, not on stdin
    script = parameters["COMMAND_STRING"]
    assert shlex.join(COMMAND) in script and '"$@"' in script
    assert context_menu.installed(platform="darwin", folder=tmp_path)

    context_menu.remove(platform="darwin", folder=tmp_path)
    context_menu.remove(platform="darwin", folder=tmp_path)  # off already: nothing to do
    assert not context_menu.installed(platform="darwin", folder=tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_the_quick_actions_script_hands_every_path_over_whole(tmp_path: Path) -> None:
    said = tmp_path / "said.txt"
    command = ["/bin/sh", "-c", 'printf "%s\\n" "$@" > "$0"', str(said)]
    context_menu.install(command, platform="darwin", folder=tmp_path)
    flow = plistlib.loads((tmp_path / "Ask Graph-Me-Maybe.workflow" / "Contents" /
                           "document.wflow").read_bytes())
    script = flow["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]

    subprocess.run(["/bin/sh", "-c", script, "sh", "/invented/a b.pdf", "/invented/c's"],
                   check=True)

    for _ in range(100):  # the script leaves the command running on its own
        if said.is_file() and said.read_text().count("\n") == 2:
            break
        time.sleep(0.05)
    assert said.read_text().splitlines() == ["/invented/a b.pdf", "/invented/c's"]


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, str]] = {}

    def CreateKey(self, root: str, path: str) -> Any:  # noqa: N802
        assert root == "HKCU"
        self.keys.setdefault(path, {})
        return _Key(self, path)

    def OpenKey(self, root: str, path: str, reserved: int = 0, access: int = 0) -> Any:  # noqa: N802
        if path not in self.keys:
            raise FileNotFoundError(path)
        return _Key(self, path)

    def SetValueEx(self, key: Any, name: str, reserved: int, kind: int, value: str) -> None:  # noqa: N802
        self.keys[key.path][name] = value

    def DeleteKey(self, root: str, path: str) -> None:  # noqa: N802
        if path not in self.keys:
            raise FileNotFoundError(path)
        assert not any(k.startswith(path + "\\") for k in self.keys), "a key with subkeys"
        del self.keys[path]


class _Key:
    def __init__(self, registry: FakeWinreg, path: str) -> None:
        self.path = path

    def __enter__(self) -> _Key:
        return self

    def __exit__(self, *_: object) -> None:
        pass


def test_on_windows_it_is_a_verb_on_files_and_folders_for_this_user() -> None:
    registry = FakeWinreg()
    command = [r"C:\Invented\pythonw.exe", "-m", "graphme.cli", "ask-window"]

    context_menu.install(command, platform="win32", registry=registry)

    for base in (r"Software\Classes\*\shell\GraphMeMaybe",
                 r"Software\Classes\Directory\shell\GraphMeMaybe"):
        assert registry.keys[base] == {"": "Ask Graph-Me-Maybe", "MultiSelectModel": "Player"}
        assert registry.keys[base + r"\command"] == {
            "": r'C:\Invented\pythonw.exe -m graphme.cli ask-window "%1"'}
    assert context_menu.installed(platform="win32", registry=registry)
    context_menu.remove(platform="win32", registry=registry)
    context_menu.remove(platform="win32", registry=registry)  # off already: nothing to do
    assert registry.keys == {}
    assert not context_menu.installed(platform="win32", registry=registry)


def test_elsewhere_there_is_no_menu_and_installing_says_why(tmp_path: Path) -> None:
    assert not context_menu.installed(platform="linux", folder=tmp_path)
    with pytest.raises(OSError, match="macOS and Windows"):
        context_menu.install(COMMAND, platform="linux", folder=tmp_path)


def test_a_home_of_its_own_has_an_entry_of_its_own_and_leaves_the_apps_alone(
    tmp_path: Path,
) -> None:
    default = context_menu.default_home(platform="darwin")
    dev = tmp_path / "Graph-Me-Maybe-dev"
    context_menu.install(["/invented/app"], home=default, platform="darwin", folder=tmp_path)
    context_menu.install(["/invented/dev", "--home", str(dev)], home=dev, platform="darwin",
                         folder=tmp_path)

    mine = tmp_path / "Ask Graph-Me-Maybe (Graph-Me-Maybe-dev).workflow" / "Contents"
    service = plistlib.loads((mine / "Info.plist").read_bytes())["NSServices"][0]
    assert service["NSMenuItem"]["default"] == "Ask Graph-Me-Maybe (Graph-Me-Maybe-dev)"
    script = plistlib.loads((mine / "document.wflow").read_bytes())[
        "actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert str(dev) in script
    assert context_menu.installed(home=dev, platform="darwin", folder=tmp_path)

    context_menu.remove(home=dev, platform="darwin", folder=tmp_path)
    assert not context_menu.installed(home=dev, platform="darwin", folder=tmp_path)
    app = tmp_path / "Ask Graph-Me-Maybe.workflow" / "Contents" / "document.wflow"
    assert "/invented/app" in plistlib.loads(app.read_bytes())[
        "actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert context_menu.installed(home=default, platform="darwin", folder=tmp_path)


def test_on_windows_a_home_of_its_own_has_a_verb_of_its_own() -> None:
    registry = FakeWinreg()
    default = context_menu.default_home(platform="win32", environ={"LOCALAPPDATA": r"C:\Inv"})
    context_menu.install(["app.exe"], home=default, platform="win32", registry=registry,
                         environ={"LOCALAPPDATA": r"C:\Inv"})
    context_menu.install(["dev.exe"], home=Path("D:/work/My Dev"), platform="win32",
                         registry=registry, environ={"LOCALAPPDATA": r"C:\Inv"})

    mine = r"Software\Classes\*\shell\GraphMeMaybe-My-Dev"
    assert registry.keys[mine][""] == "Ask Graph-Me-Maybe (My Dev)"
    context_menu.remove(home=Path("D:/work/My Dev"), platform="win32", registry=registry,
                        environ={"LOCALAPPDATA": r"C:\Inv"})
    assert r"Software\Classes\*\shell\GraphMeMaybe" in registry.keys
    assert mine not in registry.keys
