"""Graph-Me-Maybe: typed multimodal document graphs.

    import graphme
    doc = graphme.read("report.pptx")
    with graphme.GraphMe() as g:
        g.add("docs/"); g.embed()
        print(g.ask("...").text)
"""

from graphme.adapters.storage.graph_rag import GraphAnswer, GraphNotEmbedded, GraphSource
from graphme.api import GraphMe, read
from graphme.application.corpus_build import BuildReport
from graphme.application.embedded_documents import shown_name
from graphme.config import Settings, load_settings
from graphme.domain.models import ExtractedDocument

__version__ = "0.1.15"

__all__ = [
    "BuildReport", "ExtractedDocument", "GraphAnswer", "GraphMe", "GraphNotEmbedded",
    "GraphSource", "Settings", "__version__", "load_settings", "read", "shown_name",
]
