"""The right-click "Ask Graph-Me-Maybe": a Finder Quick Action on macOS, an Explorer verb on
Windows, each for this user only and needing no administrator. It runs `graphme ask-window` on
the files and folders picked. Off until switched on in Settings, as start at login is
(`login_item.py`).

Each app home has an entry of its own: the installed app's (its default home) is "Ask
Graph-Me-Maybe", any other home's "Ask Graph-Me-Maybe (<its folder name>)" -- so a checkout's
home and the installed app's never replace or remove each other's."""
from __future__ import annotations

import contextlib
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from graphme import processes

NAME = "Ask Graph-Me-Maybe"
SETTING = "GRAPHME_CONTEXT_MENU"
VERB = "GraphMeMaybe"
_SHELL_SCRIPT = "/System/Library/Automator/Run Shell Script.action"


def supported(platform: str = sys.platform) -> bool:
    return platform in ("darwin", "win32")


def command(home: Path) -> list[str]:
    """What the menu runs: `graphme ask-window` on this Python, for the app home `home` -- a
    Quick Action starts with none of the app's environment."""
    from graphme.agent import _python

    return [_python(), "-m", "graphme.cli", "ask-window", "--home", str(home)]


def default_home(platform: str = sys.platform, environ: dict[str, str] | None = None) -> Path:
    """The home the installed app opens: the platform's, whatever GRAPHME_HOME says now."""
    from graphme.config import app_home

    env = {k: v for k, v in (dict(os.environ) if environ is None else environ).items()
           if k != "GRAPHME_HOME"}
    env.setdefault("HOME", str(Path.home()))
    return app_home(env, platform)


def _own(home: Path | None, platform: str, environ: dict[str, str] | None) -> str:
    """`home`'s folder name when it is not the installed app's; "" for the app's own."""
    if home is None or home == default_home(platform, environ):
        return ""
    return home.name


def entry_name(home: Path | None, platform: str = sys.platform,
               environ: dict[str, str] | None = None) -> str:
    own = _own(home, platform, environ)
    return f"{NAME} ({own})" if own else NAME


def _bundle(folder: Path | None, name: str) -> Path:
    return (folder or Path.home() / "Library" / "Services") / f"{name}.workflow"


def _verbs(home: Path | None, platform: str, environ: dict[str, str] | None) -> tuple[str, str]:
    own = re.sub(r"[^0-9A-Za-z]+", "-", _own(home, platform, environ)).strip("-")
    verb = f"{VERB}-{own}" if own else VERB
    return (rf"Software\Classes\*\shell\{verb}", rf"Software\Classes\Directory\shell\{verb}")


def _services_changed(folder: Path | None) -> None:
    """Finder's menu reads the Services it knows of: told of a change in the user's own."""
    if folder is None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            processes.run(["/System/Library/CoreServices/pbs", "-update"], check=False,
                          timeout=30)


def _winreg(registry: Any) -> Any:
    if registry is not None:
        return registry
    import winreg  # type: ignore[import-not-found,unused-ignore]  # Windows only

    return winreg


def _workflow(script: str) -> dict[str, Any]:
    """Automator's "Run Shell Script" as a Quick Action on Finder's files and folders, each
    picked path one argument."""
    names = ("inputMethod", "CheckedForUserDefaultShell", "source", "COMMAND_STRING", "shell")
    return {
        "AMApplicationBuild": "534", "AMApplicationVersion": "2.10", "AMDocumentVersion": "2",
        "connectors": {},
        "workflowMetaData": {
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceProcessesInput": False, "processesInput": False,
            "useAutomaticInputType": False, "presentationMode": 15,
            "applicationBundleIDsByPath": {}, "applicationPaths": [],
            "systemImageName": "NSActionTemplate",
        },
        "actions": [{"isViewVisible": 1, "action": {
            "AMAccepts": {"Container": "List", "Optional": True,
                          "Types": ["com.apple.cocoa.string"]},
            "AMActionVersion": "2.0.3", "AMApplication": ["Automator"],
            "AMParameterProperties": {name: {} for name in names},
            "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
            "ActionBundlePath": _SHELL_SCRIPT, "ActionName": "Run Shell Script",
            "ActionParameters": {"inputMethod": 1, "CheckedForUserDefaultShell": True,
                                 "source": "", "COMMAND_STRING": script, "shell": "/bin/sh"},
            "BundleIdentifier": "com.apple.RunShellScript", "CFBundleVersion": "2.0.3",
            "CanShowSelectedItemsWhenRun": False, "CanShowWhenRun": True,
            "Category": ["AMCategoryUtilities"], "Class Name": "RunShellScriptAction",
            "InputUUID": "6E5D1B52-0D44-4F43-9C1D-1D7C5B7F0A01",
            "OutputUUID": "6E5D1B52-0D44-4F43-9C1D-1D7C5B7F0A02",
            "UUID": "6E5D1B52-0D44-4F43-9C1D-1D7C5B7F0A03",
            "UnlocalizedApplications": ["Automator"], "isViewVisible": 1,
            "nibPath": f"{_SHELL_SCRIPT}/Contents/Resources/Base.lproj/main.nib",
            "arguments": {str(i): {"default value": "", "name": name, "required": "0",
                                   "type": "0", "uuid": str(i)}
                          for i, name in enumerate(names)},
        }}],
    }


def install(command: list[str], *, home: Path | None = None, platform: str = sys.platform,
            folder: Path | None = None, registry: Any = None,
            environ: dict[str, str] | None = None) -> None:
    """`command` on the right-click menu of files and folders, the picked paths after it, as
    `home`'s entry (the installed app's when None)."""
    name = entry_name(home, platform, environ)
    if platform == "darwin":
        contents = _bundle(folder, name) / "Contents"
        contents.mkdir(parents=True, exist_ok=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"NSServices": [{
            "NSMenuItem": {"default": name}, "NSMessage": "runWorkflowAsService",
            "NSIconName": "NSActionTemplate", "NSSendFileTypes": ["public.item"],
            "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"}}]}))
        # Left running on its own: the Quick Action would otherwise spin in the menu bar for as
        # long as the window stays open.
        script = f'nohup {shlex.join(command)} "$@" >/dev/null 2>&1 &\n'
        (contents / "document.wflow").write_bytes(plistlib.dumps(_workflow(script)))
        _services_changed(folder)
    elif platform == "win32":
        reg = _winreg(registry)
        line = f'{subprocess.list2cmdline(command)} "%1"'
        for verb in _verbs(home, platform, environ):
            with reg.CreateKey(reg.HKEY_CURRENT_USER, verb) as key:
                reg.SetValueEx(key, "", 0, reg.REG_SZ, name)
                # Lets the verb show for several picked at once; a plain command verb still
                # starts once per file, so each picked file opens a window of its own.
                reg.SetValueEx(key, "MultiSelectModel", 0, reg.REG_SZ, "Player")
            with reg.CreateKey(reg.HKEY_CURRENT_USER, verb + r"\command") as key:
                reg.SetValueEx(key, "", 0, reg.REG_SZ, line)
    else:
        raise OSError("the right-click menu is set on macOS and Windows only")


def remove(*, home: Path | None = None, platform: str = sys.platform,
           folder: Path | None = None, registry: Any = None,
           environ: dict[str, str] | None = None) -> None:
    """`home`'s entry taken off; every other home's left as it is."""
    if platform == "darwin":
        shutil.rmtree(_bundle(folder, entry_name(home, platform, environ)), ignore_errors=True)
        _services_changed(folder)
    elif platform == "win32":
        reg = _winreg(registry)
        for verb in _verbs(home, platform, environ):
            for key in (verb + r"\command", verb):
                with contextlib.suppress(FileNotFoundError):  # off already
                    reg.DeleteKey(reg.HKEY_CURRENT_USER, key)


def installed(*, home: Path | None = None, platform: str = sys.platform,
              folder: Path | None = None, registry: Any = None,
              environ: dict[str, str] | None = None) -> bool:
    """Whether `home`'s own entry is on the menu."""
    if platform == "darwin":
        bundle = _bundle(folder, entry_name(home, platform, environ))
        return (bundle / "Contents" / "document.wflow").is_file()
    if platform == "win32":
        reg = _winreg(registry)
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, _verbs(home, platform, environ)[0]):
                return True
        except FileNotFoundError:
            return False
    return False


def setting(home: Path) -> dict[str, Any] | None:
    """`home`'s switch as the settings page shows it; None where there is no menu."""
    if not supported():
        return None
    on = installed(home=home)
    return {"env": SETTING, "secret": False, "set": on, "value": "on" if on else "off",
            "choices": {"off": "", "on": ""}}


def switch(value: str, home: Path) -> None:
    """The menu put on ("on") or taken off ("off"); `ValueError` for anything else, and where
    there is no menu."""
    if not supported():
        raise ValueError("the right-click menu is set on macOS and Windows only")
    if value not in ("on", "off"):
        raise ValueError(f"{SETTING} is on or off")
    try:
        if value == "on":
            install(command(home), home=home)
        else:
            remove(home=home)
    except OSError as error:
        raise ValueError(f"the right-click menu could not be changed: {error}") from error
