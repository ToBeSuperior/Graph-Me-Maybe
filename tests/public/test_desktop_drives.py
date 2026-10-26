"""Box Drive's folder found on this machine and listed a level at a time, never above it.
Invented folders and files."""
from __future__ import annotations

from pathlib import Path

import pytest

from graphme.adapters.sources.desktop_drives import (
    Drive,
    box_drive_root,
    google_drive_root,
    listing,
    onedrive_drive,
)


def _tree(root: Path) -> Path:
    (root / "Specs" / "Old").mkdir(parents=True)
    (root / "Specs" / "Valve Spec.docx").write_bytes(b"docx")
    (root / "Specs" / "notes.txt").write_text("not a document")
    (root / "Specs" / "~$Valve Spec.docx").write_bytes(b"lock")
    (root / "Leak Test Procedure.pdf").write_bytes(b"%PDF-1.4")
    (root / ".hidden").mkdir()
    return root


def test_on_a_mac_box_drive_is_found_under_cloud_storage(tmp_path: Path) -> None:
    (tmp_path / "Library" / "CloudStorage" / "Box-Box").mkdir(parents=True)

    assert box_drive_root(tmp_path, platform="darwin", environ={}) == \
        tmp_path / "Library" / "CloudStorage" / "Box-Box"


def test_on_windows_box_drive_is_the_box_folder_in_the_users_profile(tmp_path: Path) -> None:
    (tmp_path / "Box").mkdir()

    assert box_drive_root(Path("/elsewhere"), platform="win32",
                          environ={"USERPROFILE": str(tmp_path)}) == tmp_path / "Box"


def test_without_box_drive_nothing_is_found(tmp_path: Path) -> None:
    assert box_drive_root(tmp_path, platform="darwin", environ={}) is None
    assert box_drive_root(tmp_path, platform="win32", environ={"USERPROFILE": str(tmp_path)}) \
        is None


def test_a_folder_lists_its_folders_then_its_documents_and_where_it_is(tmp_path: Path) -> None:
    root = _tree(tmp_path / "Box")

    top = listing(root, "")
    specs = listing(root, "Specs")

    assert [(i["type"], i["name"], i["readable"]) for i in top["items"]] == [
        ("folder", "Specs", True), ("file", "Leak Test Procedure.pdf", True)]
    assert top["items"][1]["size"] == len(b"%PDF-1.4") and top["items"][1]["modified"] > 0
    assert top["path"] == [] and top["name"] == "Box"
    # Every file is shown as a folder shows it; one not read here cannot be picked.
    assert [(i["type"], i["name"], i["id"], i["readable"]) for i in specs["items"]] == [
        ("folder", "Old", "Specs/Old", True),
        ("file", "notes.txt", "Specs/notes.txt", False),
        ("file", "Valve Spec.docx", "Specs/Valve Spec.docx", True)]
    assert specs["path"] == [{"id": "", "name": "Box"}]


@pytest.mark.parametrize("outside", ["..", "../..", "Specs/../..", "/etc"])
def test_nothing_above_box_drive_is_listed(tmp_path: Path, outside: str) -> None:
    root = _tree(tmp_path / "Box")

    with pytest.raises(ValueError, match="outside"):
        listing(root, outside)


def test_on_a_mac_google_drive_is_the_accounts_folder_under_cloud_storage(tmp_path: Path) -> None:
    account = tmp_path / "Library" / "CloudStorage" / "GoogleDrive-someone@invented.test"
    (account / "My Drive").mkdir(parents=True)

    assert google_drive_root(tmp_path, platform="darwin", environ={}) == account


def test_on_windows_google_drive_is_the_drive_letter_holding_my_drive(tmp_path: Path) -> None:
    empty, mounted = tmp_path / "E", tmp_path / "G"
    empty.mkdir()
    (mounted / "내 드라이브").mkdir(parents=True)

    assert google_drive_root(Path("/elsewhere"), platform="win32", environ={},
                             volumes=[empty, mounted]) == mounted


def test_on_windows_a_mirrored_my_drive_in_the_profile_is_found(tmp_path: Path) -> None:
    (tmp_path / "My Drive").mkdir()

    assert google_drive_root(Path("/elsewhere"), platform="win32",
                             environ={"USERPROFILE": str(tmp_path)}, volumes=[]) == \
        tmp_path / "My Drive"


def test_without_google_drive_nothing_is_found(tmp_path: Path) -> None:
    assert google_drive_root(tmp_path, platform="darwin", environ={}) is None
    assert google_drive_root(tmp_path, platform="win32", environ={"USERPROFILE": str(tmp_path)},
                             volumes=[tmp_path]) is None


def test_a_drive_letter_is_named_by_itself_at_the_top(tmp_path: Path) -> None:
    # `G:\` has no name of its own: the way back to the top must still say where it goes.
    assert listing(tmp_path, "")["name"] == tmp_path.name
    top = Path(tmp_path.anchor)
    assert listing(top, "")["name"] == str(top)


def _cloud(home: Path) -> Path:
    cloud = home / "Library" / "CloudStorage"
    for name in ("OneDrive-Invented Org", "OneDrive-SharedLibraries-Invented Org", "Box-Box",
                 "GoogleDrive-someone@invented.test"):
        (cloud / name).mkdir(parents=True)
    (cloud / "OneDrive-SharedLibraries-Invented Org" / "Quality - Documents").mkdir()
    (cloud / "OneDrive-SharedLibraries-Invented Org" / "Quality - Documents" / "Plan.pdf") \
        .write_bytes(b"%PDF-1.4")
    return cloud


def test_on_a_mac_onedrive_shows_its_own_folders_and_the_sharepoint_libraries(
    tmp_path: Path,
) -> None:
    cloud = _cloud(tmp_path)

    drive = onedrive_drive(tmp_path, platform="darwin", environ={})

    assert drive == Drive(cloud, ("OneDrive-Invented Org", "OneDrive-SharedLibraries-Invented Org"))
    top = listing(drive.base, "", drive.top)
    assert [i["name"] for i in top["items"]] == list(drive.top)  # Box's and Google's not
    library = listing(drive.base, "OneDrive-SharedLibraries-Invented Org/Quality - Documents",
                      drive.top)
    assert [(i["name"], i["id"]) for i in library["items"]] == [
        ("Plan.pdf", "OneDrive-SharedLibraries-Invented Org/Quality - Documents/Plan.pdf")]


@pytest.mark.parametrize("elsewhere", ["Box-Box", "GoogleDrive-someone@invented.test", "..",
                                       "OneDrive-Invented Org/../Box-Box"])
def test_nothing_beside_onedrive_s_folders_is_listed(tmp_path: Path, elsewhere: str) -> None:
    _cloud(tmp_path)
    drive = onedrive_drive(tmp_path, platform="darwin", environ={})
    assert drive is not None

    with pytest.raises(ValueError, match="outside"):
        listing(drive.base, elsewhere, drive.top)


def test_on_windows_onedrive_shows_where_it_syncs_and_nothing_else_in_the_profile(
    tmp_path: Path,
) -> None:
    mine = tmp_path / "OneDrive - Invented Org"
    library = tmp_path / "Invented Org" / "Quality - Documents"
    for folder in (mine, library, tmp_path / "Downloads"):
        folder.mkdir(parents=True)

    drive = onedrive_drive(Path("/elsewhere"), platform="win32", environ={},
                           mounts=[str(mine), str(library)])

    assert drive == Drive(tmp_path, ("Invented Org", "OneDrive - Invented Org"))
    with pytest.raises(ValueError, match="outside"):
        listing(drive.base, "Downloads", drive.top)


def test_without_onedrive_nothing_is_found(tmp_path: Path) -> None:
    assert onedrive_drive(tmp_path, platform="darwin", environ={}) is None
    assert onedrive_drive(tmp_path, platform="win32", environ={}, mounts=[]) is None
