from __future__ import annotations

import argparse
import json
from pathlib import Path

from graphme.adapters.rendering.xlsx_measurement import measure_workbook


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure XLSX cell and visual content ranges")
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()
    for source in args.sources:
        result = measure_workbook(source)
        print(json.dumps({"source": result.source, "sha256": result.sha256, "sheets": [
            {
                "index": sheet.index,
                "name": sheet.name,
                "dimension": sheet.dimension,
                "cell_content_range": sheet.cell_content_range,
                "drawing_content_range": sheet.drawing_content_range,
                "visual_content_range": sheet.visual_content_range,
                "cell_count": sheet.cell_count,
                "drawing_count": sheet.drawing_count,
            }
            for sheet in result.sheets
        ], "warnings": result.warnings}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
