from collections.abc import Mapping, Sequence
from typing import Protocol

from graphme.domain.models import (
    DocumentReferenceRequest,
    DocumentReferenceResult,
    FigureGroupingResult,
    HeadingLine,
    HierarchyChoice,
    HierarchyScopeRequest,
    HierarchyScopeResult,
    JoinPlacement,
    PageJoinRequest,
    ScannedPage,
    UnitGroupingRequest,
    UnitGroupingResult,
    VisualCaptionRequest,
    VisualCaptionResult,
)


class VisionProvider(Protocol):
    def resolve_hierarchy_scope(
        self,
        *,
        request: HierarchyScopeRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
        preceding: bytes | None = None,
    ) -> HierarchyScopeResult: ...

    def group_page_figures(
        self,
        *,
        request: HierarchyScopeRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
    ) -> FigureGroupingResult: ...

    def group_page_units(
        self,
        *,
        request: UnitGroupingRequest,
        images: Sequence[bytes],
    ) -> UnitGroupingResult: ...

    def caption_visuals(
        self,
        *,
        request: VisualCaptionRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
    ) -> VisualCaptionResult: ...

    def find_references(
        self,
        *,
        request: DocumentReferenceRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
    ) -> DocumentReferenceResult: ...

    def read_scanned_page(self, image: bytes) -> ScannedPage: ...

    def join_page(self, *, request: PageJoinRequest) -> tuple[JoinPlacement, ...]: ...

    def outline_headings(
        self, *, lines: Sequence[HeadingLine]
    ) -> tuple[tuple[str, str | None], ...]: ...

    def choose_hierarchy_scope(
        self,
        *,
        request: HierarchyScopeRequest,
        image: bytes,
        candidates: Sequence[HierarchyScopeResult],
        unit_images: Mapping[str, bytes] | None = None,
    ) -> HierarchyChoice: ...
