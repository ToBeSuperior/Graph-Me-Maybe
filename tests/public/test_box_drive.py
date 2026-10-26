"""Box Drive's folder found on this machine and listed a level at a time, never above it.
Invented folders and files."""
from __future__ import annotations

from pathlib import Path

import pytest

from graphme.adapters.sources.box_drive import box_drive_root, listing


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
