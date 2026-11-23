"""A file written whole: two writers never share a temporary name, a failure leaves the old."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from graphme.files import write_atomic


def test_writers_at_once_each_leave_a_whole_file(tmp_path: Path) -> None:
    target = tmp_path / "mini-window.json"
    texts = [f'{{"x": {n}}}' * 200 for n in range(8)]
    failed: list[BaseException] = []

    def write(text: str) -> None:
        try:
            write_atomic(target, text)
        except BaseException as error:  # a thread's error is otherwise only printed
            failed.append(error)

    workers = [threading.Thread(target=write, args=(t,)) for t in texts]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert not failed
    assert target.read_text(encoding="utf-8") in texts
    assert [p.name for p in tmp_path.iterdir()] == ["mini-window.json"]  # no temporary left


def test_a_failed_write_keeps_the_old_file_and_its_mode(tmp_path: Path) -> None:
    target = tmp_path / "watch.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o640)

    with pytest.raises(TypeError):
        write_atomic(target, object())  # type: ignore[arg-type]
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["watch.json"]

    write_atomic(target, b"new")
    assert target.read_bytes() == b"new"
    if sys.platform != "win32":  # Windows keeps no such mode
        assert target.stat().st_mode & 0o777 == 0o640
