from pathlib import Path
from typing import Protocol

from graphme.domain.enums import DocumentFormat
from graphme.domain.models import ExtractedDocument


class DocumentExtractor(Protocol):
    format: DocumentFormat

    def extract(self, source: Path) -> ExtractedDocument: ...

