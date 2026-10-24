"""An identifier is found however a sentence goes on after it: run with GRAPHME_NEO4J_TEST=1.

The `cjk` analyzer keeps a Korean particle on the number it follows ("XQ-58의" is one token
"58의"), so a phrase for the bare identifier missed every Korean sentence naming it. Invented
sentences, under an index made for this run and removed after."""
from __future__ import annotations

import os
import uuid

import pytest

import graphme
from graphme.adapters.storage.hybrid_search import any_named, every_word, named_identifiers

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHME_NEO4J_TEST") != "1", reason="needs the local Neo4j"
)

SENTENCES = {
    "ko": "XQ-58의 시험 압력은 1.5 MPa이다",
    "en": "The test pressure of XQ-58 is 1.5 MPa",
    "other": "The test pressure of XQ-580 is 2.0 MPa",
}


def test_a_bare_identifier_finds_the_korean_sentence_naming_it_too() -> None:
    tag = uuid.uuid4().hex[:8]
    label, index = f"Probe{tag}", f"probe_{tag}"
    with graphme.GraphMe() as g:
        driver = g.graph_space().driver
        driver.execute_query(
            f"CREATE FULLTEXT INDEX {index} FOR (p:{label}) ON EACH [p.text] "
            "OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}")
        try:
            for key, text in SENTENCES.items():
                driver.execute_query(f"CREATE (:{label} {{key: $key, text: $text}})",
                                     key=key, text=text)
            driver.execute_query("CALL db.awaitIndex($index, 60)", index=index)
            # Both ask: whether anything holds the number at all, then which passages do.
            found = {
                name: [record["key"] for record in driver.execute_query(
                    "CALL db.index.fulltext.queryNodes($index, $words) YIELD node "
                    "RETURN node.key AS key", index=index, words=words).records]
                for name, words in (("held", any_named(named_identifiers("XQ-58"))),
                                    ("every", every_word("XQ-58")))
            }
        finally:
            driver.execute_query(f"MATCH (p:{label}) DETACH DELETE p")
            driver.execute_query(f"DROP INDEX {index} IF EXISTS")

    assert {name: sorted(keys) for name, keys in found.items()} == {
        "held": ["en", "ko"], "every": ["en", "ko"]}
