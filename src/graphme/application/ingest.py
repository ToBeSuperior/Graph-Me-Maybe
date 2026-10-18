from __future__ import annotations

from pathlib import Path

from graphme.domain.enums import DocumentFormat

SUFFIX_FORMATS = {
    ".xlsx": DocumentFormat.XLSX,
    ".pptx": DocumentFormat.PPTX,
    ".docx": DocumentFormat.DOCX,
    ".pdf": DocumentFormat.PDF,
}
"""The files read here, by extension."""


def format_of(source: Path) -> DocumentFormat:
    """The format a file is read as, by its extension."""
    fmt = SUFFIX_FORMATS.get(source.suffix.lower())
    if fmt is None:
        raise ValueError(f"unsupported document format: {source.suffix!r}")
    return fmt


def documents_in(folder: Path) -> list[Path]:
    """Every document under a folder that can be read, in a stable order.

    A folder is how a corpus arrives: everything in it, subfolders too. What is not one of
    the formats read here is passed over, and so is what is not the user's -- a lock file
    Office keeps beside an open document ("~$..."), anything hidden.
    """
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUFFIX_FORMATS
        and not path.name.startswith("~$")
        and not any(part.startswith(".") for part in path.relative_to(folder).parts)
    )


def documents_at(path: Path) -> list[Path]:
    """What a path holds to be read: a folder's documents (`documents_in`), or one file --
    refused here, before anything is read, if it is not a format read here."""
    if path.is_dir():
        return documents_in(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    format_of(path)
    return [path]
