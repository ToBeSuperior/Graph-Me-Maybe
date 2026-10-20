"""Each original is read from the place the graph space records. Invented files."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from graphme.adapters.sources import Sources


def test_a_path_is_read_where_it_is_and_a_gone_one_says_so(tmp_path: Path) -> None:
    here = tmp_path / "invented.pdf"
    here.write_bytes(b"%PDF-1.4")
    sources = Sources()

    with sources.open_copy(str(here)) as copy:
        assert copy == here
    with pytest.raises(FileNotFoundError), sources.open_copy(str(tmp_path / "gone.pdf")):
        pass
    assert sources.web_url(str(here)) is None


def test_a_cloud_address_goes_to_the_store_of_its_scheme(tmp_path: Path) -> None:
    class _Cloud:
        @contextmanager
        def open_copy(self, origin: str) -> Iterator[Path]:
            copy = tmp_path / "fetched.pdf"
            copy.write_bytes(b"%PDF-1.4")
            yield copy

        def web_url(self, origin: str) -> str | None:
            return "https://drive.invented/" + origin.rsplit("/", 2)[1]

    sources = Sources({"gdrive": _Cloud()})

    with sources.open_copy("gdrive://file123/report.pdf") as copy:
        assert copy.read_bytes() == b"%PDF-1.4"
    assert sources.web_url("gdrive://file123/report.pdf") == "https://drive.invented/file123"
