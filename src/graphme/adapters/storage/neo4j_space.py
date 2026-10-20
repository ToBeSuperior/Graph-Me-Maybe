"""The graph space: a Neo4j database every document of a corpus is put in.

A document goes in as `adapters/export/neo4j.py` shapes it -- `DocNode` plus its type as
labels, scalar properties, the published relationship types -- written with parameters,
never by splicing values into a query. Putting a document again replaces it: what an
earlier reading put there and this one did not is gone, and so is every link to it, until
the links are drawn again. The passages a question is searched by (`Chunk`) are kept here
too, each covering (`COVERS`) the units it was cut from, and go with that document.

The links are drawn over the space itself (`identities`, `mentions`, `put_links`), so what
is matched is every document there, whichever build put it there.
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction
from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired

from graphme.adapters.export.neo4j import (
    CHUNK_LABEL,
    NODE_LABEL,
    SCHEMA,
    graph_records,
    relationship_record,
)
from graphme.adapters.export.neo4j_import import (
    ARRAY_DELIMITER,
    CorpusRows,
    NodeRow,
    RelationshipRow,
)
from graphme.application.corpus_build import SpaceUnavailable
from graphme.application.corpus_links import DocumentIdentity, Mention, identity_of
from graphme.application.file_titles import stated_titles
from graphme.application.graph_chunks import Chunk, GraphUnit
from graphme.config import Settings
from graphme.domain.enums import EdgeType
from graphme.domain.models import ExtractedDocument, GraphEdge

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
"""A label or a relationship type, which a query cannot take as a parameter: checked before
it is written into one."""


CHUNK_TEXT_INDEX = "chunk_text"
TEXT_ANALYZER = "cjk"
"""How the passages' words are cut for the fulltext index. Lucene's standard analyzer keeps a
Korean word whole with its particle -- 압력은 is not 압력 -- so a question's words matched almost
nothing; `cjk` cuts Hangul (and Chinese, Japanese) into overlapping pairs of characters, which
match whatever ending follows. Latin words and numbers are cut as before."""


RESET_BATCH = 200
"""Documents deleted in one transaction when the space is emptied."""
LINK_BATCH = 5000
"""Mentions whose links are cleared and written again in one transaction."""


def checked_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"not a label or relationship type: {name!r}")
    return name


class PasswordWanted(RuntimeError):
    """The Neo4j at the address asks for a password this one is not, or is given wrong."""


class Neo4jGraphSpace:
    def __init__(self, driver: Driver) -> None:
        self.driver = driver
        """Shared with what reads the space beside it (`graph_rag.py`), so one pool serves both."""
        recut = self._recut_text_index()
        for statement in (
            *SCHEMA,
            # Words a question names exactly -- a document number, a file name -- which a
            # vector alone does not rank: what Neo4j's hybrid retrievers search beside it.
            f"CREATE FULLTEXT INDEX {CHUNK_TEXT_INDEX} IF NOT EXISTS "
            f"FOR (c:{CHUNK_LABEL}) ON EACH [c.text] "
            f"OPTIONS {{indexConfig: {{`fulltext.analyzer`: '{TEXT_ANALYZER}'}}}}",
        ):
            self.driver.execute_query(statement)
        if recut:  # filled again from every passage before the first question needs it
            self.driver.execute_query("CALL db.awaitIndex($name, 600)", name=CHUNK_TEXT_INDEX)

    def _recut_text_index(self) -> bool:
        """A fulltext index made with another analyzer dropped, so it is made again with
        TEXT_ANALYZER; Neo4j fills it from the passages already there. Two processes opening
        the space at once may both see it: dropped if it still exists."""
        records, _, _ = self.driver.execute_query(
            "SHOW FULLTEXT INDEXES YIELD name, options WHERE name = $name "
            "RETURN options.indexConfig['fulltext.analyzer'] AS analyzer",
            name=CHUNK_TEXT_INDEX,
        )
        if records and records[0]["analyzer"] != TEXT_ANALYZER:
            self.driver.execute_query(f"DROP INDEX {CHUNK_TEXT_INDEX} IF EXISTS")
            return True
        return False

    @classmethod
    def from_settings(cls, settings: Settings) -> Neo4jGraphSpace:
        # No password is the local Neo4j this project starts itself, with authentication off.
        auth = (settings.neo4j_user, settings.neo4j_password) if settings.neo4j_password else None
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=auth,
            # A property no node has yet is how "not embedded" reads, not a typo.
            notifications_min_severity="OFF",
        )
        try:
            driver.verify_connectivity()
        except AuthError as error:
            driver.close()
            raise PasswordWanted(
                f"the Neo4j at {settings.neo4j_uri} does not take the password -- check "
                "GRAPHME_NEO4J_PASSWORD in .env."
            ) from error
        return cls(driver)

    def close(self) -> None:
        self.driver.close()

    def put_document(self, extracted: ExtractedDocument) -> None:
        """The document as it now reads, in place of whatever was there under its id."""
        records = graph_records(extracted)
        # Where the file is, so a viewer can draw its pages again from the space alone,
        # and the titles it goes by -- the outline's and the file's own -- worked out once,
        # by the same rule every other reader of a document's identity uses. Read before the
        # transaction: it is retried, and a file is not read again for that.
        source = extracted.document.source_path
        stated = list(stated_titles(source, source.suffix))

        def write(tx: ManagedTransaction) -> None:
            _forget(tx, [extracted.document.id])
            _write_nodes(tx, records["nodes"])
            _write_relationships(tx, records["relationships"])
            tx.run(
                "MATCH (d:Document {id: $document}) "
                "SET d.source_path = $path, d.title = $title, d.stated_titles = $stated, "
                "d.modified_at = $modified",
                document=extracted.document.id,
                path=str(source),
                title=identity_of(extracted).title,
                stated=stated,
                modified=modified_at(source),
            )

        try:
            with self.driver.session() as session:
                session.execute_write(write)
        except (ServiceUnavailable, SessionExpired) as error:
            raise SpaceUnavailable(str(error)) from error

    def read_from(self, source_path: str) -> set[str]:
        records, _, _ = self.driver.execute_query(
            "MATCH (d:Document {source_path: $path}) RETURN d.id AS id", path=source_path)
        return {record["id"] for record in records}

    def relocate(self, content_hash: str, source_path: str) -> None:
        self.driver.execute_query(
            "MATCH (d:Document {content_hash: $hash}) SET d.source_path = $path",
            hash=content_hash, path=source_path)

    def source_path(self, document_id: str) -> str | None:
        """The file a document in the space was read from, as it was put there."""
        records, _, _ = self.driver.execute_query(
            "MATCH (d:Document {id: $document}) RETURN d.source_path AS path",
            document=document_id,
        )
        return records[0]["path"] if records else None

    def delete_documents(self, document_ids: Sequence[str]) -> None:
        """The documents gone from the space, with every node and passage of theirs and every
        link to or from them, in one transaction. Nothing else changes: a mention that named
        one names whatever else answers it still (each link is that pair's own). A document
        that was like one has a place free among its best, and is measured again on the next
        embedding."""

        with self.driver.session() as session:
            session.execute_write(_forget, list(document_ids))

    def identities(self) -> tuple[DocumentIdentity, ...]:
        """Every document in the space, as it was written there when it was put in."""
        records, _, _ = self.driver.execute_query(
            "MATCH (d:Document) RETURN d.id AS id, d.file_name AS file_name, d.title AS title, "
            "d.stated_titles AS stated, d.source_path AS path"
        )
        stated = {record["id"]: record["stated"] for record in records}
        # A document put before its file's titles were kept has them read off its file, once.
        missing = {record["id"]: Path(record["path"]) for record in records
                   if record["stated"] is None and record["path"]
                   and Path(record["path"]).is_file()}
        if missing:
            found = {document: list(stated_titles(path, path.suffix))
                     for document, path in missing.items()}
            self.driver.execute_query(
                "UNWIND $rows AS row MATCH (d:Document {id: row.id}) "
                "SET d.stated_titles = row.stated",
                rows=[{"id": document, "stated": titles} for document, titles in found.items()],
            )
            stated |= found
        return tuple(
            DocumentIdentity(
                document_id=record["id"],
                file_name=record["file_name"] or record["id"],
                title=record["title"],
                stated=tuple(stated[record["id"]] or ()),
            )
            for record in records
        )

    def mentions(self) -> tuple[Mention, ...]:
        records, _, _ = self.driver.execute_query(
            "MATCH (e:ExternalDocument) RETURN e.id AS id, e.document_id AS document, "
            "e.label AS name"
        )
        return tuple(
            Mention(node_id=record["id"], document_id=record["document"], name=record["name"])
            for record in records
            if record["name"]
        )

    def document_ids(self) -> list[str]:
        """Every document in the space."""
        records, _, _ = self.driver.execute_query("MATCH (d:Document) RETURN d.id AS id")
        return [record["id"] for record in records]

    def reset(self) -> int:
        """Every document gone -- the space empty, its indexes kept for what goes in next;
        how many went. A batch of documents a transaction, as they are deleted one by one."""
        documents = self.document_ids()
        for start in range(0, len(documents), RESET_BATCH):
            self.delete_documents(documents[start : start + RESET_BATCH])
        return len(documents)

    def files(self, document_ids: Sequence[str]) -> dict[str, str]:
        """The file of each of these documents the space holds."""
        records, _, _ = self.driver.execute_query(
            "UNWIND $ids AS id MATCH (d:Document {id: id}) RETURN d.id AS id, d.label AS file",
            ids=list(document_ids),
        )
        return {record["id"]: record["file"] for record in records}

    def contents(self, hashes: Sequence[str]) -> set[str]:
        """Which of these file contents (SHA-256) the space already holds."""
        records, _, _ = self.driver.execute_query(
            "UNWIND $hashes AS h MATCH (d:Document {content_hash: h}) RETURN DISTINCT h",
            hashes=list(hashes),
        )
        return {record["h"] for record in records}

    def unlinked(self, rule: str) -> set[str]:
        """The documents whose mentions were not judged by `rule` -- put since, or never."""
        records, _, _ = self.driver.execute_query(
            "MATCH (d:Document) WHERE coalesce(d.links_rule, '') <> $rule RETURN d.id AS id",
            rule=rule,
        )
        return {record["id"] for record in records}

    def mark_linked(self, documents: Sequence[str], rule: str) -> None:
        for start in range(0, len(documents), LINK_BATCH):
            self.driver.execute_query(
                "UNWIND $documents AS document MATCH (d:Document {id: document}) "
                "SET d.links_rule = $rule",
                documents=list(documents[start : start + LINK_BATCH]), rule=rule,
            )

    def put_links(self, links: Sequence[GraphEdge], *, of: Sequence[str]) -> None:
        """`links` in place of the SAME_AS drawn before from the mentions `of`, LINK_BATCH
        mentions a transaction: each batch's old links go and its new ones come together, so a
        build stopped half way leaves no mention without the link it had."""
        by_mention: dict[str, list[dict[str, Any]]] = {}
        for edge in links:
            by_mention.setdefault(edge.source_id, []).append(relationship_record(edge))

        def write(tx: ManagedTransaction, mentions: list[str]) -> None:
            tx.run(
                f"UNWIND $mentions AS mention "
                f"MATCH (:{NODE_LABEL} {{id: mention}})-[r:{EdgeType.SAME_AS.value}]->() "
                "DELETE r",
                mentions=mentions,
            )
            _write_relationships(tx, [r for m in mentions for r in by_mention.get(m, ())])

        with self.driver.session() as session:
            for start in range(0, len(of), LINK_BATCH):
                session.execute_write(write, list(of[start : start + LINK_BATCH]))

    def documents_to_cut(
        self, document_ids: Sequence[str] | None, *, rule: str
    ) -> dict[str, str]:
        """Each document with no passages cut by `rule`, and the name its passages are read
        under -- its file, which is what an answer cites."""
        records, _, _ = self.driver.execute_query(
            f"""
            MATCH (d:Document)
            WHERE ($documents IS NULL OR d.id IN $documents)
              AND NOT EXISTS {{ MATCH (:{CHUNK_LABEL} {{document_id: d.id, rule: $rule}}) }}
            RETURN d.id AS id, d.label AS name
            """,
            documents=list(document_ids) if document_ids is not None else None,
            rule=rule,
        )
        return {record["id"]: record["name"] or record["id"] for record in records}

    def units(self, document_id: str) -> list[GraphUnit]:
        """A document's units as passages are cut from them: a visual by its caption, a unit
        with no words of its own but something under it by its label."""
        records, _, _ = self.driver.execute_query(_UNITS, document=document_id)
        return [
            GraphUnit(
                id=record["id"], parent=record["parent"], text=record["text"],
                order=unit_order(record["order"]),
                attached=tuple(record["attached"]),
            )
            for record in records
        ]

    def put_chunks(self, document_id: str, chunks: Sequence[Chunk], *, rule: str) -> None:
        """A document's passages, in place of whatever it was cut into before."""

        def write(tx: ManagedTransaction) -> None:
            tx.run(
                f"MATCH (c:{CHUNK_LABEL} {{document_id: $document}}) DETACH DELETE c",
                document=document_id,
            )
            tx.run(
                f"""
                UNWIND $chunks AS chunk
                CREATE (c:{CHUNK_LABEL} {{id: chunk.id, document_id: $document,
                                          text: chunk.text, members: chunk.members,
                                          rule: $rule}})
                WITH c, chunk
                UNWIND chunk.members AS member
                MATCH (n:{NODE_LABEL} {{id: member}})
                CREATE (c)-[:COVERS]->(n)
                """,
                document=document_id,
                rule=rule,
                chunks=[
                    {"id": f"{document_id}:{index}", "text": chunk.text,
                     "members": list(chunk.member_ids)}
                    for index, chunk in enumerate(chunks)
                ],
            )

        with self.driver.session() as session:
            session.execute_write(write)

    def document_map(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Which documents there are, how many others each names, and how they are tied:
        one entry per citing document and the document it names, with how many mentions."""
        documents, _, _ = self.driver.execute_query(
            """
            MATCH (d:Document)
            // DocNode: the label document_id is indexed under -- without it, every mention
            // in the space is read once for every document.
            OPTIONAL MATCH (e:DocNode:ExternalDocument {document_id: d.id})
            RETURN d.id AS id, d.label AS label, d.title AS title, count(e) AS mentions,
                   d.source_path AS source_path, d.modified_at AS modified_at,
                   d.content_hash AS content_hash
            ORDER BY label
            """
        )
        links, _, _ = self.driver.execute_query(
            """
            MATCH (e:ExternalDocument)-[r:SAME_AS]->(d:Document)
            RETURN e.document_id AS from, d.id AS to, count(r) AS count,
                   collect(DISTINCT r.meta_matched_by) AS matched_by
            ORDER BY from, to
            """
        )
        return [dict(record) for record in documents], [dict(record) for record in links]

    def similar_links(self) -> list[dict[str, Any]]:
        """Documents related by what they say: one entry a pair, with how alike they are."""
        records, _, _ = self.driver.execute_query(
            """
            MATCH (a:Document)-[r:SIMILAR_TO]->(b:Document)
            RETURN a.id AS from, b.id AS to, r.score AS score
            ORDER BY from, to
            """
        )
        return [dict(record) for record in records]

    def document_graph(self, document_id: str) -> dict[str, list[dict[str, Any]]]:
        """One document's own graph, in the records shape the exporter writes -- without its
        centroid, which is for searching, not for reading."""
        records = self._records(
            f"MATCH (n:{NODE_LABEL} {{document_id: $document}})",
            f"MATCH (a:{NODE_LABEL} {{document_id: $document}})-[r]->"
            f"(b:{NODE_LABEL} {{document_id: $document}})",
            document=document_id,
        )
        for node in records["nodes"]:
            node["properties"] = _readable(node["properties"])
        return records

    def chunk_spans(self, document_id: str) -> list[dict[str, Any]]:
        """A document's passages as the outline shows them: each by the units it covers, and
        how central it is to the document (`importance`, None until it is embedded)."""
        records, _, _ = self.driver.execute_query(
            f"MATCH (c:{CHUNK_LABEL} {{document_id: $document}}) "
            "RETURN c.id AS id, c.members AS members, c.importance AS importance "
            "ORDER BY toInteger(split(c.id, ':')[-1])",
            document=document_id,
        )
        return [dict(record) for record in records]

    def corpus(
        self, document_ids: Sequence[str] | None = None, *, passages: bool = True
    ) -> dict[str, list[dict[str, Any]]]:
        """Everything in the space, in the records shape the exporter writes: the graph, and
        beside it the passages with their vectors, what each covers, and the indexes that
        search them -- so an empty Neo4j loaded from it is searched as this one is.

        `document_ids` keeps to those documents: their nodes and passages, and only the links
        between two of them -- what leaves can be chosen, and a link to a document left
        behind does not leave either.

        Held whole, so for a small space -- `--json` and `--cypher`; a bundle streams
        (`corpus_rows`). A document's centroid is left out: a Cypher script parses every value
        it carries, and the centroid is for searching, not for reading."""
        rows = self.corpus_rows(document_ids, passages=passages)
        records: dict[str, list[dict[str, Any]]] = {
            "nodes": [], "relationships": [], "chunks": [], "covers": [],
            "indexes": list(rows.indexes),
        }
        for labels, node_id, properties in rows.nodes():
            if labels == CHUNK_LABEL:
                records["chunks"].append({"id": node_id, "properties": properties})
            else:
                records["nodes"].append({"id": node_id, "labels": labels.split(ARRAY_DELIMITER),
                                         "properties": _readable(properties)})
        for kind, start, end, properties in rows.relationships():
            if kind == "COVERS":
                records["covers"].append({"chunk": start, "node": end})
            else:
                records["relationships"].append({"id": properties["id"], "type": kind,
                                                 "start": start, "end": end,
                                                 "properties": properties})
        return records

    def _indexes(self) -> list[dict[str, Any]]:
        """The vector and fulltext indexes over the passages, as the schema remakes them."""
        indexes, _, _ = self.driver.execute_query(
            "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, options "
            "WHERE type IN ['VECTOR', 'FULLTEXT'] AND labelsOrTypes = [$label] "
            "RETURN name, type, properties, options ORDER BY name",
            label=CHUNK_LABEL,
        )
        return [_index(record) for record in indexes]

    def corpus_rows(
        self, document_ids: Sequence[str] | None = None, *, passages: bool = True
    ) -> CorpusRows:
        """What `corpus` holds, as rows read from the space while they are written -- each
        pass a stream of its own, so a space bigger than memory still leaves whole.
        `passages=False` is the graph alone: the passages and their vectors are not read."""
        chosen = list(document_ids) if document_ids is not None else None

        def stream(query: str) -> Iterator[Any]:
            with self.driver.session() as session:
                yield from session.run(query, chosen=chosen)

        def nodes() -> Iterator[NodeRow]:
            for record in stream(
                f"MATCH (n:{NODE_LABEL}) WHERE $chosen IS NULL OR n.document_id IN $chosen "
                "RETURN n.id AS id, labels(n) AS labels, properties(n) AS properties"
            ):
                labels = [NODE_LABEL, *sorted(set(record["labels"]) - {NODE_LABEL})]
                # Where the file lies on this machine stays on it.
                properties = {k: v for k, v in record["properties"].items() if k != "source_path"}
                yield ARRAY_DELIMITER.join(labels), record["id"], properties
            if not passages:
                return
            for record in stream(
                f"MATCH (c:{CHUNK_LABEL}) WHERE $chosen IS NULL OR c.document_id IN $chosen "
                "RETURN c.id AS id, properties(c) AS properties"
            ):
                yield CHUNK_LABEL, record["id"], record["properties"]

        def relationships() -> Iterator[RelationshipRow]:
            for record in stream(
                f"MATCH (a:{NODE_LABEL})-[r]->(b:{NODE_LABEL}) WHERE $chosen IS NULL "
                "OR (a.document_id IN $chosen AND b.document_id IN $chosen) "
                "RETURN r.id AS id, type(r) AS type, a.id AS start, b.id AS end, "
                "properties(r) AS properties"
            ):
                yield (record["type"], record["start"], record["end"],
                       {**record["properties"], "id": record["id"]})
            if not passages:
                return
            for record in stream(
                f"MATCH (c:{CHUNK_LABEL})-[:COVERS]->(n:{NODE_LABEL}) "
                "WHERE $chosen IS NULL OR c.document_id IN $chosen "
                "RETURN c.id AS chunk, n.id AS node"
            ):
                yield "COVERS", record["chunk"], record["node"], {}

        documents, _, _ = self.driver.execute_query(
            "MATCH (d:Document) WHERE $chosen IS NULL OR d.id IN $chosen RETURN count(d) AS n",
            chosen=chosen,
        )
        return CorpusRows(nodes=nodes, relationships=relationships,
                          indexes=self._indexes(), documents=documents[0]["n"])

    def _records(self, nodes_match: str, relationships_match: str, **parameters: Any) -> dict[
        str, list[dict[str, Any]]
    ]:
        nodes, _, _ = self.driver.execute_query(
            f"{nodes_match} RETURN n.id AS id, labels(n) AS labels, properties(n) AS properties "
            f"ORDER BY {READING_ORDER}, n.id",
            **parameters,
        )
        relationships, _, _ = self.driver.execute_query(
            f"{relationships_match} RETURN r.id AS id, type(r) AS type, a.id AS start, "
            "b.id AS end, properties(r) AS properties",
            **parameters,
        )
        return {
            "nodes": [
                {
                    "id": record["id"],
                    "labels": [NODE_LABEL, *sorted(set(record["labels"]) - {NODE_LABEL})],
                    "properties": record["properties"],
                }
                for record in nodes
            ],
            "relationships": [dict(record) for record in relationships],
        }


def _write_nodes(tx: ManagedTransaction, nodes: Sequence[dict[str, Any]]) -> None:
    by_labels: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for node in nodes:
        extra = tuple(checked_name(label) for label in node["labels"] if label != NODE_LABEL)
        by_labels.setdefault(extra, []).append(
            {"id": node["id"], "properties": node["properties"]}
        )
    for extra, rows in by_labels.items():
        labels = "".join(f":{label}" for label in extra)
        tx.run(
            f"UNWIND $rows AS row MERGE (n:{NODE_LABEL} {{id: row.id}}) "
            f"SET n{labels} SET n += row.properties",
            rows=rows,
        )


def _write_relationships(
    tx: ManagedTransaction, relationships: Sequence[dict[str, Any]]
) -> None:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for relationship in relationships:
        by_type.setdefault(checked_name(relationship["type"]), []).append(relationship)
    for kind, rows in by_type.items():
        tx.run(
            f"UNWIND $rows AS row "
            f"MATCH (a:{NODE_LABEL} {{id: row.start}}), (b:{NODE_LABEL} {{id: row.end}}) "
            f"MERGE (a)-[r:{kind} {{id: row.id}}]->(b) SET r += row.properties",
            rows=rows,
        )


def modified_at(path: Path | str) -> float | None:
    """When the file was last changed, as a folder shows it -- read where it lies; None when
    it is not there to read."""
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def _forget(tx: ManagedTransaction, document_ids: list[str]) -> None:
    """Every node and passage of these documents gone, with every link to or from them -- the
    one way a document leaves, deleted or put in again. A document that was like one has a
    place free among its best, and is measured again on the next embedding."""
    tx.run(
        "UNWIND $ids AS id MATCH (:Document {id: id})-[:SIMILAR_TO]-(like:Document) "
        "REMOVE like.similarity_rule",
        ids=document_ids,
    )
    # Label by label, each by its own document_id index.
    for label in (CHUNK_LABEL, NODE_LABEL):
        tx.run(f"UNWIND $ids AS id MATCH (n:{label} {{document_id: id}}) DETACH DELETE n",
               ids=document_ids)


def _readable(properties: dict[str, Any]) -> dict[str, Any]:
    """A node's properties without its centroid, which is for searching, not for reading."""
    return {k: v for k, v in properties.items() if not k.endswith("_centroid")}


def _index(record: Any) -> dict[str, Any]:
    index = {
        "kind": record["type"],
        "name": record["name"],
        "label": CHUNK_LABEL,
        "property": record["properties"][0],
    }
    config = record["options"]["indexConfig"]
    if record["type"] == "VECTOR":
        index["dimensions"] = int(config["vector.dimensions"])
        index["similarity"] = str(config["vector.similarity_function"]).lower()
    else:
        analyzer = str(config.get("fulltext.analyzer") or TEXT_ANALYZER)
        # Written into schema.cypher as it is: an analyzer's name, nothing else.
        index["analyzer"] = analyzer if re.fullmatch(r"[A-Za-z0-9_-]+", analyzer) else TEXT_ANALYZER
    return index


def unit_order(values: Sequence[Any]) -> tuple[float, ...]:
    """A unit's place as `READING_ORDER` gives it, flat: a slide's paragraph records its own
    as [shape, paragraph], which reads as the shape's place and then the paragraph's."""
    return tuple(
        float(part)
        for value in values
        for part in (value if isinstance(value, list) else (value,))
    )


READING_ORDER = """[coalesce(n.page, n.slide, 0),
        coalesce(n.meta_reading_order, n.meta_source_order, n.meta_body_index,
                 n.bbox[1], 0)]"""
"""Where a node stands in its document: what each format recorded -- the page and its
reading order, the slide and its shapes, the body index -- and the top of its box where
nothing else was."""
# ponytail: mixed order keys (index vs box top) can misplace a visual among text on a
# page; write an ordinal from the extraction order at put time if that starts to matter.

_UNITS = f"""
MATCH (n:{NODE_LABEL} {{document_id: $document}})
WHERE NOT n:Document AND NOT n:ExternalDocument AND NOT n:VlmEnrichedCaption
MATCH (p)-[:PARENT_OF]->(n)
WITH n, CASE WHEN p:Document THEN null ELSE p.id END AS parent,
     COLLECT {{ MATCH (c:VlmEnrichedCaption)-[:CAPTION_OF]->(n) RETURN c.text }} AS captions,
     COLLECT {{ MATCH (c:VlmEnrichedCaption)-[:CAPTION_OF]->(n) RETURN c.id }} AS attached,
     EXISTS {{ MATCH (n)-[:PARENT_OF]->() }} AS holds
RETURN n.id AS id, parent, attached,
       coalesce(n.text,
                CASE WHEN size(captions) > 0
                     THEN trim(reduce(s = '', t IN captions | s + ' ' + t)) END,
                CASE WHEN holds THEN n.label END, '') AS text,
       {READING_ORDER} AS order
"""
