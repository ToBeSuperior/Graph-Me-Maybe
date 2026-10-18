from enum import StrEnum


class DocumentFormat(StrEnum):
    XLSX = "xlsx"
    PPTX = "pptx"
    DOCX = "docx"
    PDF = "pdf"


class NodeType(StrEnum):
    DOCUMENT = "document"
    WORKBOOK = "workbook"
    WORKSHEET = "worksheet"
    TABLE = "table"
    PIVOT_TABLE = "pivot_table"
    CHART = "chart"
    SLIDE = "slide"
    # No extractor emits these three any more -- text is TEXT_CHUNK and what leads what is
    # the outline pass's. They stay so graphs stored before that still load.
    SECTION = "section"
    HEADING = "heading"
    TEXT_CHUNK = "text_chunk"
    IMAGE = "image"
    CAPTION = "caption"
    VLM_ENRICHED_CAPTION = "vlm_enriched_caption"
    EXTERNAL_DOCUMENT = "external_document"


class EdgeType(StrEnum):
    CONTAINS = "CONTAINS"
    PARENT_OF = "PARENT_OF"
    NEXT = "NEXT"
    SAME_SHEET = "SAME_SHEET"
    USES_VALUE_FROM = "USES_VALUE_FROM"
    REFERENCES = "REFERENCES"
    CAPTION_OF = "CAPTION_OF"
    RELATED_TO = "RELATED_TO"
    OUTLINE_PARENT = "OUTLINE_PARENT"
    REGION_MEMBER = "REGION_MEMBER"
    REGION_PARENT = "REGION_PARENT"
    ORDER_BEFORE = "ORDER_BEFORE"
    CONTINUES = "CONTINUES"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    TABLE_HEADER_OF = "TABLE_HEADER_OF"
    COMPARES_WITH = "COMPARES_WITH"
    SAME_AS = "SAME_AS"


class EvidenceKind(StrEnum):
    SOURCE_TEXT = "source_text"
    SOURCE_VALUES = "source_values"
    SOURCE_MEDIA = "source_media"
    SOURCE_METADATA = "source_metadata"
    RENDERED_PREVIEW = "rendered_preview"
    GENERATED_EXTRACTION = "generated_extraction"
    GENERATED_CAPTION = "generated_caption"


class SemanticUnitKind(StrEnum):
    TABLE = "table"
    BODY_TEXT = "body_text"
    FIGURE = "figure"
    MEMO = "memo"
    CAPTION = "caption"
    VLM_ENRICHED_CAPTION = "vlm_enriched_caption"
    EXTERNAL_DOCUMENT = "external_document"
    HEADING = "heading"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
