"""A table or chart preview is drawn with a font that has Hangul. Matplotlib's own font has
none, and every Korean label came out as boxes -- in the picture the model is shown too.
Invented data."""

from __future__ import annotations

import warnings

from openpyxl import Workbook

from graphme.adapters.extractors.excel import ExcelExtractor


def test_a_korean_table_preview_draws_every_glyph() -> None:
    sheet = Workbook().active
    sheet.append(["시료 번호", "판정"])
    sheet.append(["V50-0001", "합격"])
    extractor = ExcelExtractor.__new__(ExcelExtractor)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        png, _ = extractor._render_table_png(label="시험 결과", sheet=sheet, fallback=sheet,
                                             ref="A1:B2")

    assert png.startswith(b"\x89PNG")
    assert [str(w.message) for w in caught if "missing from font" in str(w.message)] == []
