"""Documents linked by what they say (`SIMILAR_TO`), measured in the graph space.

A new document's passages each ask the vector index for their nearest passages of other
documents, as a question would; the documents those belong to are candidates, and the ones
found most often and nearest are measured against it (`passage_scores.relatedness`). The
cosines are taken inside Neo4j and only each passage's nearest in the other document comes
back, so a document costs the same in a space of ten documents or of ten thousand.

A link has no direction -- one line a pair, measured the same from either end -- and a
document keeps its SIMILAR_TOP best: a document already full gives up its weakest link to a
better one, and refuses a worse.
"""
from __future__ import annotations

from collections.abc import Sequence

import neo4j
import numpy as np

from graphme.adapters.export.neo4j import CHUNK_LABEL
from graphme.adapters.storage.neo4j_space import checked_name
from graphme.application.passage_scores import relatedness

NEAREST_PASSAGES = 10
"""Passages of other documents each passage of a new document asks the vector index for:
the documents they belong to are the ones it may be measured against."""
OWN_PASSAGES_ASKED = 50
"""How many of the index's first answers a document's own passages may take: past that, a
passage of a long document finds fewer of the others, rather than the index answering a
square of the document."""
SIMILAR_CANDIDATES = 60
"""Of those documents, how many are measured: the ones its passages found most often and
nearest. Measuring costs a cosine for every pair of their passages."""
SIMILAR_AT = 0.8
"""How related two documents must be to be linked (`relatedness`, a cosine)."""
SIMILAR_TOP = 20
"""The most documents one document is linked to by likeness: its best."""
SIMILARITY_RULE = (
    f"nearest-both-ways/importance-product/ann{NEAREST_PASSAGES}+{OWN_PASSAGES_ASKED}"
    f"/of{SIMILAR_CANDIDATES}-near/at{SIMILAR_AT}/top{SIMILAR_TOP}"
)
"""Names how documents are measured and linked, every setting above in it: stored on each
document measured, so a change in any of them measures every document again."""


CLEAR_BATCH = 1000
"""Documents whose links are cleared in one transaction before they are measured again."""


class SimilarDocuments:
    def __init__(self, driver: neo4j.Driver, *, index: str, vector_property: str) -> None:
        self._driver = driver
        self._index = checked_name(index)
        self._vector = f"`{checked_name(vector_property)}`"

    def relate(self, document_ids: Sequence[str] | None) -> None:
        """Each document not measured by this rule linked anew -- rating a document's
        passages takes its measure away."""
        records, _, _ = self._driver.execute_query(
            f"""
            MATCH (d:Document)
            WHERE ($documents IS NULL OR d.id IN $documents)
              AND coalesce(d.similarity_rule, '') <> $rule
              AND EXISTS {{ MATCH (c:{CHUNK_LABEL} {{document_id: d.id}})
                           WHERE c.{self._vector} IS NOT NULL }}
            RETURN d.id AS id ORDER BY id
            """,
            documents=list(document_ids) if document_ids is not None else None,
            rule=SIMILARITY_RULE,
        )
        documents = [record["id"] for record in records]
        # Every link of the documents measured goes first, once: measured one after another,
        # a later one would otherwise take away a link an earlier one had just made.
        for start in range(0, len(documents), CLEAR_BATCH):
            self._driver.execute_query(
                "UNWIND $documents AS document "
                "MATCH (:Document {id: document})-[r:SIMILAR_TO]-() DELETE r",
                documents=documents[start : start + CLEAR_BATCH],
            )
        for document in documents:
            self._link(document)

    def _link(self, document: str) -> None:
        # ponytail: a document that gives up a link is not measured again for one it may now
        # have room for; it is, the next time it is embedded or the rule changes.
        scored = sorted(
            ((score, other) for other, score in self._measured(document).items()
             if score >= SIMILAR_AT),
            reverse=True,
        )
        # The links this document and its candidates hold, read once; who gives up which is
        # worked out here, and written in one go.
        ends = [document, *(other for _, other in scored)]
        records, _, _ = self._driver.execute_query(
            "UNWIND $ends AS end MATCH (:Document {id: end})-[r:SIMILAR_TO]-(o:Document) "
            "RETURN end, elementId(r) AS link, r.score AS score, o.id AS other",
            ends=ends,
        )
        links: dict[str, tuple[float, str, str]] = {}
        held: dict[str, set[str]] = {end: set() for end in ends}
        for r in records:
            links[r["link"]] = (r["score"], r["end"], r["other"])
            held[r["end"]].add(r["link"])

        def weakest(end: str) -> tuple[float, str | None]:
            mine = held.get(end, set())
            if len(mine) < SIMILAR_TOP:
                return -1.0, None
            link = min(mine, key=lambda x: links[x][0])
            return links[link][0], link

        def give_up(link: str) -> None:
            _, one, two = links.pop(link)
            for end in (one, two):
                held.get(end, set()).discard(link)

        gone: list[str] = []
        made: list[dict[str, object]] = []
        for score, other in scored:
            if any(links[x][2] == other or links[x][1] == other for x in held[document]):
                continue  # made from the other end earlier in this pass
            mine, theirs = weakest(document), weakest(other)
            if mine[1] and score <= mine[0]:
                break  # full of better links, and the rest are weaker still
            if theirs[1] and score <= theirs[0]:
                continue
            for _, link in (mine, theirs):
                if link and link in links:
                    give_up(link)
                    gone.append(link)
            one, two = sorted((document, other))
            pair = f"similar:{one}|{two}"
            links[pair] = (score, document, other)
            held[document].add(pair)
            held[other].add(pair)
            made.append({"one": one, "two": two, "id": pair, "score": score})

        def write(tx: neo4j.ManagedTransaction) -> None:
            tx.run("MATCH (d:Document {id: $document}) SET d.similarity_rule = $rule",
                   document=document, rule=SIMILARITY_RULE)
            tx.run("MATCH ()-[r:SIMILAR_TO]->() WHERE elementId(r) IN $gone DELETE r",
                   gone=[x for x in gone if not x.startswith("similar:")])
            tx.run(
                "UNWIND $made AS m MATCH (a:Document {id: m.one}), (b:Document {id: m.two}) "
                "CREATE (a)-[:SIMILAR_TO {id: m.id, score: m.score, rule: $rule}]->(b)",
                made=[m for m in made if m["id"] in links], rule=SIMILARITY_RULE,
            )

        with self._driver.session() as session:
            session.execute_write(write)

    def _measured(self, document: str) -> dict[str, float]:
        """How related `document` is to each of its candidates. Each side's vectors are read
        once, the cosine of every pair taken inside Neo4j, and each passage's nearest in the
        other document picked from that matrix here."""
        vector = self._vector
        records, _, _ = self._driver.execute_query(
            f"""
            MATCH (q:{CHUNK_LABEL} {{document_id: $document}}) WHERE q.{vector} IS NOT NULL
            WITH collect(q) AS asking
            CALL (asking) {{
              UNWIND asking AS q
              // The index answers the document's own passages first: asked for that many
              // more, up to OWN_PASSAGES_ASKED, or a long document asks for a square of itself.
              CALL db.index.vector.queryNodes($index, $near + CASE WHEN size(asking) < $own
                                                     THEN size(asking) ELSE $own END, q.{vector})
              YIELD node, score
              WITH node.document_id AS other, score WHERE other <> $document
              WITH other, max(score) AS nearest, count(*) AS found
              // A score is a mean of pair cosines, none nearer than the nearest found: a
              // document found no nearer than SIMILAR_AT cannot reach it.
              WHERE 2 * nearest - 1 >= $at
              RETURN other ORDER BY nearest DESC, found DESC, other LIMIT $candidates
            }}
            WITH [q IN asking | [q.id, q.{vector}, coalesce(q.importance, 1.0)]] AS mine,
                 collect(other) AS others
            UNWIND others AS other
            CALL (other) {{
              MATCH (c:{CHUNK_LABEL} {{document_id: other}}) WHERE c.{vector} IS NOT NULL
              RETURN collect([c.id, c.{vector}, coalesce(c.importance, 1.0)]) AS theirs
            }}
            RETURN other, [m IN mine | m[0]] AS mine_ids, [m IN mine | m[2]] AS mine_weights,
                   [t IN theirs | t[0]] AS their_ids, [t IN theirs | t[2]] AS their_weights,
                   [m IN mine | [t IN theirs |
                     coalesce(2 * vector.similarity.cosine(m[1], t[1]) - 1, -1.0)]]
                     AS cosines
            """,
            document=document, index=self._index, near=NEAREST_PASSAGES,
            own=OWN_PASSAGES_ASKED, at=SIMILAR_AT, candidates=SIMILAR_CANDIDATES,
        )
        return {r["other"]: relatedness(_nearest_pairs(r)) for r in records}


def _nearest_pairs(record: neo4j.Record) -> list[tuple[str, str, float, float, float]]:
    """Each passage of either document with its nearest in the other, from the matrix of
    their cosines: (passage of one, passage of the other, cosine, importance of each)."""
    cosines = np.asarray(record["cosines"], dtype=float)
    pairs = {(i, int(cosines[i].argmax())) for i in range(cosines.shape[0])}
    pairs |= {(int(cosines[:, j].argmax()), j) for j in range(cosines.shape[1])}
    return [(record["mine_ids"][i], record["their_ids"][j], float(cosines[i, j]),
             record["mine_weights"][i], record["their_weights"][j]) for i, j in sorted(pairs)]
