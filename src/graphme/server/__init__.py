"""The HTTP server (`graphme serve`): the pipeline and the graph space over `/v1`."""
from graphme.server.app import create_app

__all__ = ["create_app"]
