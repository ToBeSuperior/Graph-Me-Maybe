from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from io import BytesIO

import numpy as np
from PIL import Image, UnidentifiedImageError

from graphme.domain.enums import SemanticUnitKind
from graphme.domain.models import (
    DocumentReferenceRequest,
    DocumentReferenceResult,
    FigureGroupingResult,
    HierarchyChoice,
    HierarchyScopeRequest,
    HierarchyScopeResult,
    JsonValue,
    NormalizedBBox,
    PageSegmentationRequest,
    PageSegmentationResult,
    SegmentedUnit,
    UnitGroupingRequest,
    UnitGroupingResult,
    VisualCaptionRequest,
    VisualCaptionResult,
)


class DeterministicVisionProvider:
    """Deterministic rendered-pixel segmentation without semantic inference."""

    _MAX_REGIONS = 80

    @property
    def model(self) -> str:
        return "deterministic-local-pixel-segmentation-v1"

    def segment_page(
        self,
        *,
        request: PageSegmentationRequest,
        image: bytes,
    ) -> PageSegmentationResult:
        try:
            with Image.open(BytesIO(image)) as decoded:
                if decoded.format != "PNG":
                    raise ValueError("offline page segmentation requires a PNG image")
                pixels = np.asarray(decoded.convert("RGB"), dtype=np.int16)
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("offline page segmentation requires a valid PNG image") from exc

        height, width = pixels.shape[:2]
        if (width, height) != (request.width, request.height):
            raise ValueError("segmentation image dimensions must match the request")

        mask, threshold = self._ink_mask(pixels)
        boxes = self._detect_regions(mask)
        units = tuple(
            self._unit(request, box, index, mask, threshold)
            for index, box in enumerate(boxes[: self._MAX_REGIONS], start=1)
        )
        return PageSegmentationResult.from_request(request, units=units)

    @staticmethod
    def _ink_mask(pixels: np.ndarray) -> tuple[np.ndarray, float]:
        height, width = pixels.shape[:2]
        border = max(1, min(12, min(height, width) // 40))
        samples = np.concatenate(
            (
                pixels[:border].reshape(-1, 3),
                pixels[-border:].reshape(-1, 3),
                pixels[:, :border].reshape(-1, 3),
                pixels[:, -border:].reshape(-1, 3),
            )
        )
        background = np.median(samples, axis=0)
        distance = np.max(np.abs(pixels - background), axis=2).astype(np.float32)
        border_distance = np.max(np.abs(samples - background), axis=1)
        noise_floor = float(np.percentile(border_distance, 99))
        nonzero = distance[distance > noise_floor]
        adaptive = float(np.percentile(nonzero, 20)) if nonzero.size else 255.0
        threshold = max(12.0, noise_floor + 8.0, min(42.0, adaptive * 0.65))
        mask = distance >= threshold

        # Suppress isolated compression/speckle noise, while retaining thin rules.
        neighbours = np.zeros(mask.shape, dtype=np.uint8)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                shifted = np.roll(mask, (dy, dx), axis=(0, 1))
                if dy < 0:
                    shifted[dy:] = False
                elif dy > 0:
                    shifted[:dy] = False
                if dx < 0:
                    shifted[:, dx:] = False
                elif dx > 0:
                    shifted[:, :dx] = False
                neighbours += shifted
        return mask & (neighbours >= 1), threshold

    @classmethod
    def _detect_regions(cls, ink: np.ndarray) -> list[tuple[int, int, int, int]]:
        height, width = ink.shape
        if not ink.any():
            return []

        # Join glyphs into lines and intersecting table/grid rules. The horizontal
        # radius remains local so whitespace between columns cannot disappear.
        joined = ink.copy()
        horizontal = max(2, min(14, width // 100))
        vertical = max(1, min(4, height // 250 + 1))
        for offset in range(1, horizontal + 1):
            joined[:, offset:] |= ink[:, :-offset]
            joined[:, :-offset] |= ink[:, offset:]
        horizontal_joined = joined.copy()
        for offset in range(1, vertical + 1):
            joined[offset:] |= horizontal_joined[:-offset]
            joined[:-offset] |= horizontal_joined[offset:]

        boxes = cls._components(joined)
        minimum_area = max(8, (height * width) // 200_000)
        boxes = [box for box in boxes if cls._ink_count(ink, box) >= minimum_area]
        boxes = cls._merge_lines(boxes)

        pad_x = max(2, width // 300)
        pad_y = max(2, height // 300)
        padded = [
            (
                max(0, x0 - pad_x),
                max(0, y0 - pad_y),
                min(width, x1 + pad_x),
                min(height, y1 + pad_y),
            )
            for x0, y0, x1, y1 in boxes
        ]
        return sorted(padded, key=lambda box: (box[1], box[0], box[3], box[2]))

    @staticmethod
    def _components(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
        height, width = mask.shape
        visited = np.zeros(mask.shape, dtype=bool)
        boxes: list[tuple[int, int, int, int]] = []
        for y, x in np.argwhere(mask):
            if visited[y, x]:
                continue
            queue: deque[tuple[int, int]] = deque([(int(y), int(x))])
            visited[y, x] = True
            x0 = x1 = int(x)
            y0 = y1 = int(y)
            while queue:
                cy, cx = queue.popleft()
                x0, x1 = min(x0, cx), max(x1, cx)
                y0, y1 = min(y0, cy), max(y1, cy)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if (
                        0 <= ny < height
                        and 0 <= nx < width
                        and mask[ny, nx]
                        and not visited[ny, nx]
                    ):
                        visited[ny, nx] = True
                        queue.append((ny, nx))
            boxes.append((x0, y0, x1 + 1, y1 + 1))
        return boxes

    @staticmethod
    def _ink_count(mask: np.ndarray, box: tuple[int, int, int, int]) -> int:
        x0, y0, x1, y1 = box
        return int(mask[y0:y1, x0:x1].sum())

    @staticmethod
    def _merge_lines(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
        pending = sorted(boxes, key=lambda box: (box[1], box[0]))
        changed = True
        while changed:
            changed = False
            merged: list[tuple[int, int, int, int]] = []
            while pending:
                current = pending.pop(0)
                x0, y0, x1, y1 = current
                current_width = x1 - x0
                for index, other in enumerate(pending):
                    ox0, oy0, ox1, oy1 = other
                    gap = max(0, oy0 - y1, y0 - oy1)
                    overlap = max(0, min(x1, ox1) - max(x0, ox0))
                    narrow_width = max(1, min(current_width, ox1 - ox0))
                    line_height = max(1, min(y1 - y0, oy1 - oy0))
                    if gap <= max(8, line_height * 2) and overlap / narrow_width >= 0.35:
                        pending.pop(index)
                        pending.insert(0, (min(x0, ox0), min(y0, oy0), max(x1, ox1), max(y1, oy1)))
                        changed = True
                        break
                else:
                    merged.append(current)
            pending = sorted(merged, key=lambda box: (box[1], box[0]))
        return pending

    @staticmethod
    def _unit(
        request: PageSegmentationRequest,
        box: tuple[int, int, int, int],
        reading_order: int,
        mask: np.ndarray,
        threshold: float,
    ) -> SegmentedUnit:
        x0, y0, x1, y1 = box
        width, height = request.width, request.height
        ink_pixels = int(mask[y0:y1, x0:x1].sum())
        area = (x1 - x0) * (y1 - y0)
        detector = "pillow-numpy-adaptive-ink-v1"
        structured_data: JsonValue = {
            "detector": detector,
            "interpretation": "local_rendered_content_region",
            "pixel_bbox": [x0, y0, x1, y1],
            "ink_pixels": ink_pixels,
            "ink_density": round(ink_pixels / area, 6),
            "threshold": round(threshold, 3),
        }
        return SegmentedUnit(
            kind=SemanticUnitKind.BODY_TEXT,
            reading_order=reading_order,
            label=f"Page {request.page_index} local content region {reading_order}",
            bbox=NormalizedBBox(x0=x0 / width, y0=y0 / height, x1=x1 / width, y1=y1 / height),
            interpreted_text=(
                f"Deterministically detected local rendered content region {reading_order}; "
                "no OCR or semantic classification was performed."
            ),
            structured_data=structured_data,
            confidence=0.75,
        )

    def resolve_hierarchy_scope(
        self,
        *,
        request: HierarchyScopeRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
        preceding: bytes | None = None,
    ) -> HierarchyScopeResult:
        """Offline mode requires explicit replay; it never invents hierarchy."""
        del request, image, preceding
        raise RuntimeError("offline hierarchy requires a replay result")

    def find_references(
        self,
        *,
        request: DocumentReferenceRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
    ) -> DocumentReferenceResult:
        """Reading a page for what it names is a reading, and offline makes none."""
        del request, image, unit_images
        return DocumentReferenceResult(references=())

    def group_page_figures(
        self,
        *,
        request: HierarchyScopeRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
    ) -> FigureGroupingResult:
        """Which units are one figure is read off the page, and offline reads no page."""
        del request, image, unit_images
        return FigureGroupingResult()

    def group_page_units(
        self,
        *,
        request: UnitGroupingRequest,
        images: Sequence[bytes],
    ) -> UnitGroupingResult:
        """Which pieces are one unit is read off the page, and offline reads no page."""
        del request, images
        return UnitGroupingResult()

    def caption_visuals(
        self,
        *,
        request: VisualCaptionRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
    ) -> VisualCaptionResult:
        """A caption is a description of a picture, and offline describes nothing."""
        del request, image, unit_images
        return VisualCaptionResult(captions=())

    def choose_hierarchy_scope(
        self,
        *,
        request: HierarchyScopeRequest,
        image: bytes,
        candidates: Sequence[HierarchyScopeResult],
        unit_images: Mapping[str, bytes] | None = None,
    ) -> HierarchyChoice:
        """Choosing between readings is a judgement, and offline makes none."""
        del request, image, candidates, unit_images
        raise RuntimeError("offline hierarchy requires a replay result")