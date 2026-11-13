"""Where each drive's desktop client keeps its folder, found in invented homes."""
from __future__ import annotations

import json
from pathlib import Path

from graphme.adapters.sources.desktop_drives import (
    DESKTOP_DRIVES,
    Drive,
    dropbox_drive,
    icloud_drive_root,
    synology_drive_root,
)


def _made(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True)


def test_dropbox_is_where_its_info_file_says_personal_and_business_together(
    tmp_path: Path,
) -> None:
    home, personal, team = tmp_path, tmp_path / "Dropbox", tmp_path / "Dropbox (Invented Co)"
    _made(personal, team, home / ".dropbox")
    (home / ".dropbox" / "info.json").write_text(json.dumps(
        {"personal": {"path": str(personal)}, "business": {"path": str(team)}}))

    assert dropbox_drive(home, platform="darwin", environ={}) == Drive(
        home, ("Dropbox", "Dropbox (Invented Co)"))


def test_dropbox_on_windows_reads_the_info_file_in_appdata(tmp_path: Path) -> None:
    folder, appdata = tmp_path / "Dropbox", tmp_path / "AppData"
    _made(folder, appdata / "Dropbox")
    (appdata / "Dropbox" / "info.json").write_text(json.dumps({"personal": {"path": str(folder)}}))

    assert dropbox_drive(tmp_path, platform="win32",
                         environ={"APPDATA": str(appdata)}) == Drive(folder)


def test_dropbox_without_an_info_file_is_looked_for_where_it_installs(tmp_path: Path) -> None:
    folder = tmp_path / "Library" / "CloudStorage" / "Dropbox"
    _made(folder)

    assert dropbox_drive(tmp_path, platform="darwin", environ={}) == Drive(folder)
    assert dropbox_drive(tmp_path / "nobody", platform="darwin", environ={}) is None


def test_icloud_drive_is_found_on_each_platform(tmp_path: Path) -> None:
    mac = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    windows = tmp_path / "profile" / "iCloudDrive"
    _made(mac, windows)

    assert icloud_drive_root(tmp_path, platform="darwin", environ={}) == mac
    assert icloud_drive_root(tmp_path, platform="win32",
                             environ={"USERPROFILE": str(tmp_path / "profile")}) == windows
    assert icloud_drive_root(tmp_path, platform="win32", environ={}) is None


def test_synology_drive_is_found_on_each_platform(tmp_path: Path) -> None:
    mac = tmp_path / "Library" / "CloudStorage" / "SynologyDrive-office"
    windows = tmp_path / "profile" / "SynologyDrive"
    _made(mac, windows)

    assert synology_drive_root(tmp_path, platform="darwin", environ={}) == mac
    assert synology_drive_root(tmp_path, platform="win32",
                               environ={"USERPROFILE": str(tmp_path / "profile")}) == windows


def test_every_drive_the_app_names_is_one_it_can_look_for() -> None:
    assert set(DESKTOP_DRIVES) == {"box", "google", "onedrive", "dropbox", "icloud", "synology"}
