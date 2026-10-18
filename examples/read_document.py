"""Read one document and print its outline, as the model read it.

    python examples/read_document.py <file>

Needs the model's key (README, step 4): every page is shown to the vision model. No Neo4j.
"""
from __future__ import annotations

import argparse

import graphme


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="a DOCX, XLSX, PPTX or PDF file")
    args = parser.parse_args()
    try:
        read = graphme.read(args.path)
    except (ValueError, OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from None

    parent = {edge.target_id: edge.source_id for edge in read.edges
              if edge.type.value == "OUTLINE_PARENT"}

    def depth(node_id: str) -> int:
        return 1 + depth(parent[node_id]) if node_id in parent else 0

    print(f"{read.document.file_name}: {len(read.nodes)} nodes, {len(read.edges)} edges")
    for node in read.nodes:
        if node.type.value == "text_chunk":
            print("  " * depth(node.id) + "- " + node.label)


if __name__ == "__main__":
    main()
