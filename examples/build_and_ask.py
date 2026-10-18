"""Put documents in the graph space, embed them, and ask a question.

    python examples/build_and_ask.py <folder or file> "<question>"

Needs the model's key and the embedder's key (README, step 4), and Neo4j: with Docker
running, one is started when none answers (README, "그래프 DB(Neo4j)는 어떻게 뜨나").
"""
from __future__ import annotations

import argparse

import graphme


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="a folder of documents, or one document")
    parser.add_argument("question")
    args = parser.parse_args()
    try:
        with graphme.GraphMe() as graph:
            built = graph.add(args.path)
            print(f"read {built.processed}, failed {len(built.failed)}, links {built.links}")
            print(f"embedded {graph.embed()}")
            answer = graph.ask(args.question)
    except (ValueError, OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    print(answer.text)
    for source in answer.sources:
        print(f"- {source.file} ({source.score:.2f})")


if __name__ == "__main__":
    main()
