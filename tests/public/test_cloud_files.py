"""A cloud drive's file kept online only takes no room here until it is read; once read, the
drive keeps it. What a build read is given back to the cloud, and what a drive keeps here is
measured -- without asking the cloud for folders never opened. Invented folders."""
from __future__ import annotations

from pathlib import Path

from graphme.adapters.sources import cloud_files


def _drive(root: Path) -> Path:
    for folder in ("Kept", "Never opened", "Kept/Deep"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "Kept" / "plan.pdf").write_bytes(b"x" * 5000)
    (root / "Kept" / "Deep" / "memo.docx").write_bytes(b"y" * 3000)
    (root / "Kept" / "online.pdf").write_bytes(b"")  # a placeholder: its bytes are in the cloud
    (root / "Never opened" / "far.pdf").write_bytes(b"z" * 7000)
    (root / ".hidden").write_bytes(b"h" * 100)
    return root


def test_what_a_drive_keeps_here_is_counted_and_folders_never_opened_are_not_walked(
    tmp_path: Path,
) -> None:
    root = _drive(tmp_path / "Drive")
    walked: list[str] = []

    def online(path: Path) -> bool:
        walked.append(path.name)
        return path.name in ("online.pdf", "Never opened")

    used = cloud_files.local_usage(root, None, online_only=online, size=lambda p: p.stat().st_size)

    assert used == {"bytes": 8000, "files": 2}
    assert "far.pdf" not in walked  # the cloud is never asked for what is inside


def test_only_the_drive_s_own_folders_are_counted(tmp_path: Path) -> None:
    root = _drive(tmp_path / "CloudStorage")

    used = cloud_files.local_usage(root, ("Kept",), online_only=lambda p: False,
                                   size=lambda p: p.stat().st_size)

    assert used == {"bytes": 8000, "files": 3}
