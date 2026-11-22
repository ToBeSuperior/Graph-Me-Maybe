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
    graph_records,
    relationship_record,
    schema,
)
from graphme.adapters.export.neo4j_import import (
    ARRAY_DELIMITER,
    CorpusRows,
    NodeRow,
    RelationshipRow,
)
from graphme.adapters.storage.workspace_scope import DEFAULT_SCOPE, WorkspaceScope
from graphme.application import versions
from graphme.application.corpus_build import SpaceUnavailable, origin_of
from graphme.application.corpus_links import (
    CARRIED_NAME,
    DocumentIdentity,
    Mention,
    identity_of,
)
from graphme.application.file_titles import stated_titles
from graphme.application.graph_chunks import Chunk, GraphUnit
from graphme.application.versions import VERSIONS_RULE, Member, Signed
from graphme.application.visual_crops import CROP_ASSET_KEY
from graphme.config import Settings
from graphme.domain.enums import EdgeType
from graphme.domain.models import ExtractedDocument, GraphEdge

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
"""A label or a relationship type, which a query cannot take as a parameter: checked before
it is written into one."""


TEXT_ANALYZER = "cjk"
"""How the passages' words are cut for the fulltext index. Lucene's standard analyzer keeps a
Korean word whole with its particle -- 압력은 is not 압력 -- so a question's words matched almost
nothing; `cjk` cuts Hangul (and Chinese, Japanese) into overlapping pairs of characters, which
match whatever ending follows. Latin words and numbers are cut as before."""


RESET_BATCH = 200
"""Documents deleted in one transaction when the space is emptied."""
LINK_BATCH = 5000
"""Mentions whose links are cleared and written again in one transaction."""
DROP_BATCH = 5000
"""Nodes of a deleted workspace deleted in one transaction."""


def checked_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"not a label or relationship type: {name!r}")
    return name


class PasswordWanted(RuntimeError):
    """The Neo4j at the address asks for a password this one is not, or is given wrong."""


class Neo4jGraphSpace:
    """One workspace's graph (`scope`) in the one Neo4j: every statement it sends goes
    through `_query` or `_run`, which name the workspace's labels in every node pattern."""

    def __init__(self, driver: Driver, *, scope: WorkspaceScope = DEFAULT_SCOPE) -> None:
        self.driver = driver
        """Shared with what reads the space beside it (`graph_rag.py`), so one pool serves both."""
        self.scope = scope
        recut = self._recut_text_index()
        # The workspace's own constraints and indexes. The global ones an older graph has
        # (`doc_node_id`, `chunk_id`, ...) are no longer made; moving a graph drops them.
        for statement in (
            *schema(scope),
            # Words a question names exactly -- a document number, a file name -- which a
            # vector alone does not rank: what Neo4j's hybrid retrievers search beside it.
            f"CREATE FULLTEXT INDEX {scope.text_index} IF NOT EXISTS "
            f"FOR (c:{scope.chunk_label}) ON EACH [c.text] "
            f"OPTIONS {{indexConfig: {{`fulltext.analyzer`: '{TEXT_ANALYZER}'}}}}",
        ):
            self._query(statement)
        if recut:  # filled again from every passage before the first question needs it
            self._query("CALL db.awaitIndex($name, 600)", name=scope.text_index)

    def _query(self, query: str, **parameters: Any) -> Any:
        """`execute_query`, held to this workspace (`WorkspaceScope.cypher`)."""
        return self.driver.execute_query(self.scope.cypher(query), **parameters)

    def _recut_text_index(self) -> bool:
        """A fulltext index made with another analyzer dropped, so it is made again with
        TEXT_ANALYZER; Neo4j fills it from the passages already there. Two processes opening
        the space at once may both see it: dropped if it still exists."""
        records, _, _ = self._query(
            "SHOW FULLTEXT INDEXES YIELD name, options WHERE name = $name "
            "RETURN options.indexConfig['fulltext.analyzer'] AS analyzer",
            name=self.scope.text_index,
        )
        if records and records[0]["analyzer"] != TEXT_ANALYZER:
            self._query(f"DROP INDEX {self.scope.text_index} IF EXISTS")
            return True
        return False

    @classmethod
    def from_settings(cls, settings: Settings) -> Neo4jGraphSpace:
        # No password is a Neo4j asking for none: the app's own, from before it had one.
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
        return cls(driver, scope=WorkspaceScope(settings.workspace))

    def within(self, scope: WorkspaceScope) -> Neo4jGraphSpace:
        """Another workspace's graph over this connection: nothing started, its schema made."""
        return Neo4jGraphSpace(self.driver, scope=scope)

    def drop_workspace(self, scope: WorkspaceScope) -> None:
        """Every node and passage of `scope`'s workspace deleted, DROP_BATCH a transaction, then
        its constraints and indexes dropped. Other workspaces' nodes, which may share its ids,
        carry other labels and are left as they are."""
        for label in (scope.chunk_label, scope.node_label):
            while self.driver.execute_query(
                f"MATCH (n:{label}) WITH n LIMIT $batch DETACH DELETE n RETURN count(*) AS gone",
                batch=DROP_BATCH,
            ).records[0]["gone"]:
                pass
        for statement in schema(scope):
            kind, name = statement.split()[1:3]  # CREATE CONSTRAINT|INDEX <name> ...
            self.driver.execute_query(f"DROP {kind} {name} IF EXISTS")
        for index in (scope.text_index, scope.vector_index):
            self.driver.execute_query(f"DROP INDEX {index} IF EXISTS")

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

        document, path = extracted.document.id, origin_of(extracted)

        def write(tx: ManagedTransaction) -> None:
            # The documents read from its file before -- an edited file is a new id, and
            # those go once it is put (`build_graph`) -- and the ones they replaced: a scope
            # or an ungroup naming any of them follows the file (`replaces`, `current`).
            before = list(tx.run(self.scope.cypher(
                "MATCH (d:Document) WHERE d.id = $document OR d.source_path = $path "
                "RETURN d.id AS id, coalesce(d.replaces, []) AS replaces"),
                document=document, path=path))
            ids = sorted({document, *(record["id"] for record in before)})
            replaces = sorted({record["id"] for record in before}
                              | {old for record in before for old in record["replaces"]}
                              - {document})
            # A pair its owner ungrouped stays ungrouped when the document is read again, or
            # edited and read as a new one.
            split = [record["other"] for record in tx.run(self.scope.cypher(
                "UNWIND $ids AS id MATCH (:Document {id: id})-[r:SIMILAR_VERSION]-(o:Document) "
                "WHERE r.split_by_user AND NOT o.id IN $ids RETURN DISTINCT o.id AS other"),
                ids=ids)]
            _forget(tx, self.scope, [document])
            _write_nodes(tx, self.scope, records["nodes"])
            _write_relationships(tx, self.scope, records["relationships"])
            _run(tx, self.scope,
                 "UNWIND $others AS other MATCH (d:Document {id: $document}), "
                 "(o:Document {id: other}) "
                 "MERGE (d)-[:SIMILAR_VERSION {split_by_user: true}]->(o)",
                 document=document, others=split)
            _run(
                tx, self.scope,
                "MATCH (d:Document {id: $document}) "
                "SET d.source_path = $path, d.title = $title, d.stated_titles = $stated, "
                "d.modified_at = $modified, d.replaces = $replaces",
                document=document,
                path=path,
                replaces=replaces or None,
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
        records, _, _ = self._query(
            "MATCH (d:Document {source_path: $path}) RETURN d.id AS id", path=source_path)
        return {record["id"] for record in records}

    def source_paths(self) -> dict[str, str]:
        """Every document by the file it was read from."""
        records, _, _ = self._query(
            "MATCH (d:Document) WHERE d.source_path IS NOT NULL "
            "RETURN d.id AS id, d.source_path AS path")
        return {record["id"]: record["path"] for record in records}

    def relocate(self, content_hash: str, source_path: str) -> None:
        self._query(
            "MATCH (d:Document {content_hash: $hash}) SET d.source_path = $path",
            hash=content_hash, path=source_path)

    def source_path(self, document_id: str) -> str | None:
        """The file a document in the space was read from, as it was put there."""
        records, _, _ = self._query(
            "MATCH (d:Document {id: $document}) RETURN d.source_path AS path",
            document=document_id,
        )
        return records[0]["path"] if records else None

    def current(self, document_ids: Sequence[str]) -> dict[str, str]:
        """Each of `document_ids` to the document the graph holds for it now: itself, or the
        one read from its file since it was edited (`replaces`); one deleted since is left
        out."""
        # ponytail: every document's `replaces` read for a scoped question; index the old ids
        # (a node per id) if a space of tens of thousands makes this slow.
        records, _, _ = self._query(
            "MATCH (d:Document) WHERE d.id IN $ids OR any(old IN d.replaces WHERE old IN $ids) "
            "RETURN d.id AS id, [old IN coalesce(d.replaces, []) WHERE old IN $ids] AS was",
            ids=list(document_ids))
        asked = set(document_ids)
        now: dict[str, str] = {}
        for record in records:
            now.update(dict.fromkeys(record["was"], record["id"]))
        now.update({record["id"]: record["id"] for record in records if record["id"] in asked})
        return now

    def delete_documents(self, document_ids: Sequence[str]) -> None:
        """The documents gone from the space, with every node and passage of theirs and every
        link to or from them, in one transaction. Nothing else changes: a mention that named
        one names whatever else answers it still (each link is that pair's own). A document
        that was like one has a place free among its best, and is measured again on the next
        embedding."""

        with self.driver.session() as session:
            session.execute_write(_forget, self.scope, list(document_ids))

    def carried_only_by(self, message_ids: Sequence[str]) -> set[str]:
        """The attachment documents that these messages carry and nothing kept carries, and
        the files carried inside those, at any depth: what goes with them when they are
        deleted. An attachment is found as a message's mention (`ExternalDocument`) that
        `SAME_AS` names it; a file inside one, as the attachment's mention written under the
        carried file's name (`embedded_name`) -- an attachment merely citing a document by
        name does not carry it. Only a message, or a file carried in one, carries: what keeps
        one is such a carrier not deleted, never another document naming it; a message named
        by another is not an attachment."""
        going = set(message_ids)
        while True:
            records, _, _ = self._query(
                "MATCH (x:ExternalDocument)-[:SAME_AS]->(a:Document) "
                "WHERE x.document_id IN $ids AND NOT a.id IN $ids "
                "AND a.meta_mail_message_id IS NULL "
                "AND EXISTS { MATCH (c:Document {id: x.document_id}) "
                "WHERE c.meta_mail_message_id IS NOT NULL "
                "OR (c.file_name =~ $carried AND x.label =~ $carried) } "
                "AND NOT EXISTS { MATCH (y:ExternalDocument)-[:SAME_AS]->(a) "
                "MATCH (m:Document {id: y.document_id}) WHERE NOT m.id IN $ids "
                "AND (m.meta_mail_message_id IS NOT NULL "
                "OR (m.file_name =~ $carried AND y.label =~ $carried)) } "
                "RETURN DISTINCT a.id AS id",
                ids=sorted(going), carried=CARRIED_NAME)
            found = {record["id"] for record in records}
            if not found:
                return going - set(message_ids)
            going |= found

    def identities(self) -> tuple[DocumentIdentity, ...]:
        """Every document in the space, as it was written there when it was put in."""
        records, _, _ = self._query(
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
            self._query(
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
        records, _, _ = self._query(
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
        records, _, _ = self._query("MATCH (d:Document) RETURN d.id AS id")
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
        records, _, _ = self._query(
            "UNWIND $ids AS id MATCH (d:Document {id: id}) RETURN d.id AS id, d.label AS file",
            ids=list(document_ids),
        )
        return {record["id"]: record["file"] for record in records}

    def crop_hashes(self) -> set[str]:
        """The crop assets the space's nodes name -- the only assets anything reads once a
        document is in (`read_node_crop`, `kept_from`, an answer's pictures)."""
        records, _, _ = self._query(
            f"MATCH (n:DocNode) WHERE n.meta_{CROP_ASSET_KEY} IS NOT NULL "
            f"RETURN DISTINCT n.meta_{CROP_ASSET_KEY} AS h")
        return {str(record["h"]) for record in records}

    def passage_texts(self) -> Iterator[str]:
        """The text of every passage the space holds, for the sweep of kept vectors."""
        records, _, _ = self._query(f"MATCH (c:{CHUNK_LABEL}) RETURN c.text AS t")
        return (str(record["t"]) for record in records if record["t"] is not None)

    def contents(self, hashes: Sequence[str]) -> set[str]:
        """Which of these file contents (SHA-256) the space already holds."""
        records, _, _ = self._query(
            "UNWIND $hashes AS h MATCH (d:Document {content_hash: h}) RETURN DISTINCT h",
            hashes=list(hashes),
        )
        return {record["h"] for record in records}

    def documents_of(self, hashes: Sequence[str]) -> dict[str, str]:
        """The documents of these file contents (SHA-256), by content."""
        records, _, _ = self._query(
            "UNWIND $hashes AS h MATCH (d:Document {content_hash: h}) "
            "RETURN h, d.id AS id", hashes=list(hashes),
        )
        return {record["h"]: record["id"] for record in records}

    def unlinked(self, rule: str) -> set[str]:
        """The documents whose mentions were not judged by `rule` -- put since, or never."""
        records, _, _ = self._query(
            "MATCH (d:Document) WHERE coalesce(d.links_rule, '') <> $rule RETURN d.id AS id",
            rule=rule,
        )
        return {record["id"] for record in records}

    def mark_linked(self, documents: Sequence[str], rule: str) -> None:
        for start in range(0, len(documents), LINK_BATCH):
            self._query(
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
            _run(
                tx, self.scope,
                f"UNWIND $mentions AS mention "
                f"MATCH (:{NODE_LABEL} {{id: mention}})-[r:{EdgeType.SAME_AS.value}]->() "
                "DELETE r",
                mentions=mentions,
            )
            _write_relationships(tx, self.scope,
                                 [r for m in mentions for r in by_mention.get(m, ())])

        with self.driver.session() as session:
            for start in range(0, len(of), LINK_BATCH):
                session.execute_write(write, list(of[start : start + LINK_BATCH]))

    def documents_to_cut(
        self, document_ids: Sequence[str] | None, *, rule: str
    ) -> dict[str, str]:
        """Each document with no passages cut by `rule`, and the name its passages are read
        under -- its file, which is what an answer cites."""
        records, _, _ = self._query(
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
        records, _, _ = self._query(_UNITS, document=document_id)
        return [
            GraphUnit(
                id=record["id"], parent=record["parent"], text=record["text"],
                order=unit_order(record["order"]),
                attached=tuple(record["attached"]),
                quote_of=record["quote_of"],
            )
            for record in records
        ]

    def put_chunks(self, document_id: str, chunks: Sequence[Chunk], *, rule: str) -> None:
        """A document's passages, in place of whatever it was cut into before, and their
        hashes (`versions.passage_hashes`) and signature on the document."""
        keys = versions.passage_hashes(chunk.text for chunk in chunks)

        def write(tx: ManagedTransaction) -> None:
            _run(
                tx, self.scope,
                f"MATCH (c:{CHUNK_LABEL} {{document_id: $document}}) DETACH DELETE c",
                document=document_id,
            )
            _run(
                tx, self.scope,
                f"""
                UNWIND $chunks AS chunk
                CREATE (c:{CHUNK_LABEL} {{id: chunk.id, document_id: $document,
                                          text: chunk.text, members: chunk.members,
                                          quote_of: chunk.quote_of, rule: $rule}})
                WITH c, chunk
                UNWIND chunk.members AS member
                MATCH (n:{NODE_LABEL} {{id: member}})
                CREATE (c)-[:COVERS]->(n)
                """,
                document=document_id,
                rule=rule,
                chunks=[
                    {"id": f"{document_id}:{index}", "text": chunk.text,
                     "members": list(chunk.member_ids), "quote_of": chunk.quote_of}
                    for index, chunk in enumerate(chunks)
                ],
            )
            # What its versions are found by, kept with the passages they come from; its
            # grouping goes with the passages it was made from.
            _run(tx, self.scope,
                 "MATCH (d:Document {id: $document}) "
                 "SET d.version_keys = $keys, d.version_minhash = $signature, "
                 "d.version_keys_rule = $keys_rule REMOVE d.versions_rule",
                 document=document_id, keys=keys, signature=versions.signature(keys),
                 keys_rule=versions.KEYS_RULE)

        with self.driver.session() as session:
            session.execute_write(write)

    def document_map(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Which documents there are, how many others each names, and how they are tied:
        one entry per citing document and the document it names, with how many mentions."""
        documents, _, _ = self._query(
            """
            MATCH (d:Document)
            // DocNode: scoped, the label document_id is indexed under -- without it, every mention
            // in the space is read once for every document.
            OPTIONAL MATCH (e:DocNode:ExternalDocument {document_id: d.id})
            RETURN d.id AS id, d.label AS label, d.title AS title, count(e) AS mentions,
                   d.source_path AS source_path, d.modified_at AS modified_at,
                   d.content_hash AS content_hash, d.meta_web_url AS web_url
            ORDER BY label
            """
        )
        links, _, _ = self._query(
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
        records, _, _ = self._query(
            """
            MATCH (a:Document)-[r:SIMILAR_TO]->(b:Document)
            RETURN a.id AS from, b.id AS to, r.score AS score
            ORDER BY from, to
            """
        )
        return [dict(record) for record in records]

    def group_versions(self) -> int:
        """Copies and versions joined by `SIMILAR_VERSION` for each document cut since it was
        last grouped by VERSIONS_RULE (`put_chunks` takes the stamp away); how many pairs.
        Every cut document's signature is read, and the passage hashes of the pairs its bands
        pick, never a passage's text -- a build costs what it changed. Only the pairs of those
        documents are written again; a pair its owner split (`ungroup`) keeps its edge and its
        mark. Nothing is asked of a model."""
        records, _, _ = self._query(
            f"""
            MATCH (d:Document)
            RETURN d.id AS id, d.content_hash AS hash, d.version_minhash AS signature,
                   d.version_keys IS NOT NULL
                     AND coalesce(d.version_keys_rule, '') = $keys_rule AS keyed,
                   coalesce(d.versions_rule, '') = $rule AS stamped,
                   EXISTS {{ MATCH (:{CHUNK_LABEL} {{document_id: d.id}}) }} AS cut
            """, rule=VERSIONS_RULE, keys_rule=versions.KEYS_RULE)
        cut = {r["id"]: r for r in records if r["cut"]}
        fresh = sorted(i for i, r in cut.items() if not r["stamped"])
        if not fresh:
            return 0
        signatures = {i: list(r["signature"] or []) for i, r in cut.items()}
        # Passages cut before their hashes were kept on the document, or hashed another way:
        # hashed once, from text.
        unsigned = [i for i in fresh if not cut[i]["keyed"]
                    or len(signatures[i]) not in (0, versions.PERMUTATIONS)]
        if unsigned:
            texts, _, _ = self._query(
                f"MATCH (d:Document) WHERE d.id IN $ids "
                f"OPTIONAL MATCH (c:{CHUNK_LABEL} {{document_id: d.id}}) "
                "RETURN d.id AS id, collect(c.text) AS texts", ids=unsigned)
            rows = [{"id": r["id"], "keys": keys, "signature": versions.signature(keys)}
                    for r in texts for keys in [versions.passage_hashes(r["texts"])]]
            self._query("UNWIND $rows AS row MATCH (d:Document {id: row.id}) "
                        "SET d.version_keys = row.keys, d.version_minhash = row.signature, "
                        "d.version_keys_rule = $keys_rule", rows=rows, keys_rule=versions.KEYS_RULE)
            signatures.update({row["id"]: row["signature"] for row in rows})
        copies, measure = versions.candidates(
            [Signed(i, r["hash"], signatures[i]) for i, r in cut.items()], fresh)
        ends = sorted({i for pair in measure for i in pair})
        held: dict[str, list[int]] = {}
        if ends:
            keys, _, _ = self._query(
                "MATCH (d:Document) WHERE d.id IN $ids RETURN d.id AS id, d.version_keys AS keys",
                ids=ends)
            held = {r["id"]: r["keys"] or [] for r in keys}
        pairs = versions.confirmed(copies, measure, held)

        def write(tx: ManagedTransaction) -> None:
            _run(tx, self.scope,
                 "UNWIND $fresh AS id MATCH (:Document {id: id})-[r:SIMILAR_VERSION]-(:Document) "
                 "WHERE r.split_by_user IS NULL DELETE r", fresh=fresh)
            _run(tx, self.scope,
                 "UNWIND $pairs AS p MATCH (a:Document {id: p[0]}), (b:Document {id: p[1]}) "
                 "MERGE (a)-[:SIMILAR_VERSION]-(b)", pairs=[list(p) for p in pairs])
            _run(tx, self.scope,
                 "UNWIND $fresh AS id MATCH (d:Document {id: id}) SET d.versions_rule = $rule",
                 fresh=fresh, rule=VERSIONS_RULE)

        with self.driver.session() as session:
            session.execute_write(write)
        return len(pairs)

    def version_pairs(self) -> list[tuple[Member, Member]]:
        """The documents `SIMILAR_VERSION` joins and their owner left grouped, a pair each."""
        records, _, _ = self._query(
            "MATCH (a:Document)-[r:SIMILAR_VERSION]->(b:Document) WHERE r.split_by_user IS NULL "
            "RETURN a.id AS one, a.label AS one_file, a.modified_at AS one_modified, "
            "b.id AS two, b.label AS two_file, b.modified_at AS two_modified")
        return [(Member(r["one"], r["one_file"], r["one_modified"]),
                 Member(r["two"], r["two_file"], r["two_modified"])) for r in records]

    def ungroup(self, document_id: str) -> int:
        """The document taken out of its family: each of its `SIMILAR_VERSION` pairs marked
        `split_by_user`, which a later grouping keeps -- it is never paired with them directly
        again, though another pair can still bring it into a family; how many pairs."""
        records, _, _ = self._query(
            "MATCH (:Document {id: $document})-[r:SIMILAR_VERSION]-(:Document) "
            "SET r.split_by_user = true RETURN count(r) AS split", document=document_id)
        return int(records[0]["split"]) if records else 0

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
        records, _, _ = self._query(
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
        indexes, _, _ = self._query(
            "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, options "
            "WHERE type IN ['VECTOR', 'FULLTEXT'] AND labelsOrTypes = [$label] "
            "RETURN name, type, properties, options ORDER BY name",
            label=self.scope.chunk_label,
        )
        return [_index(record, self.scope.chunk_label) for record in indexes]

    def corpus_rows(
        self, document_ids: Sequence[str] | None = None, *, passages: bool = True
    ) -> CorpusRows:
        """What `corpus` holds, as rows read from the space while they are written -- each
        pass a stream of its own, so a space bigger than memory still leaves whole.
        `passages=False` is the graph alone: the passages and their vectors are not read."""
        chosen = list(document_ids) if document_ids is not None else None

        def stream(query: str) -> Iterator[Any]:
            with self.driver.session() as session:
                yield from session.run(self.scope.cypher(query), chosen=chosen)

        def nodes() -> Iterator[NodeRow]:
            for record in stream(
                f"MATCH (n:{NODE_LABEL}) WHERE $chosen IS NULL OR n.document_id IN $chosen "
                "RETURN n.id AS id, labels(n) AS labels, properties(n) AS properties"
            ):
                labels = self._labels(record["labels"])
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

        documents, _, _ = self._query(
            "MATCH (d:Document) WHERE $chosen IS NULL OR d.id IN $chosen RETURN count(d) AS n",
            chosen=chosen,
        )
        return CorpusRows(nodes=nodes, relationships=relationships,
                          indexes=self._indexes(), documents=documents[0]["n"], scope=self.scope)

    def _labels(self, labels: Sequence[str]) -> list[str]:
        """A node's labels as the records read them: `DocNode`, then its type -- the
        workspace's own label left out, since it says where the node is, not what it is."""
        return [NODE_LABEL, *sorted(set(labels) - {NODE_LABEL, self.scope.node_label})]

    def _records(self, nodes_match: str, relationships_match: str, **parameters: Any) -> dict[
        str, list[dict[str, Any]]
    ]:
        nodes, _, _ = self._query(
            f"{nodes_match} RETURN n.id AS id, labels(n) AS labels, properties(n) AS properties "
            f"ORDER BY {READING_ORDER}, n.id",
            **parameters,
        )
        relationships, _, _ = self._query(
            f"{relationships_match} RETURN r.id AS id, type(r) AS type, a.id AS start, "
            "b.id AS end, properties(r) AS properties",
            **parameters,
        )
        return {
            "nodes": [
                {
                    "id": record["id"],
                    "labels": self._labels(record["labels"]),
                    "properties": record["properties"],
                }
                for record in nodes
            ],
            "relationships": [dict(record) for record in relationships],
        }


def _run(tx: ManagedTransaction, scope: WorkspaceScope, query: str, **parameters: Any) -> None:
    """`tx.run`, held to the workspace (`WorkspaceScope.cypher`)."""
    tx.run(scope.cypher(query), **parameters)


def _write_nodes(tx: ManagedTransaction, scope: WorkspaceScope,
                 nodes: Sequence[dict[str, Any]]) -> None:
    by_labels: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for node in nodes:
        extra = tuple(checked_name(label) for label in node["labels"] if label != NODE_LABEL)
        by_labels.setdefault(extra, []).append(
            {"id": node["id"], "properties": node["properties"]}
        )
    for extra, rows in by_labels.items():
        labels = "".join(f":{label}" for label in extra)
        _run(
            tx, scope,
            f"UNWIND $rows AS row MERGE (n:{NODE_LABEL} {{id: row.id}}) "
            f"SET n{labels} SET n += row.properties",
            rows=rows,
        )


def _write_relationships(
    tx: ManagedTransaction, scope: WorkspaceScope, relationships: Sequence[dict[str, Any]]
) -> None:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for relationship in relationships:
        by_type.setdefault(checked_name(relationship["type"]), []).append(relationship)
    for kind, rows in by_type.items():
        _run(
            tx, scope,
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


def _forget(tx: ManagedTransaction, scope: WorkspaceScope, document_ids: list[str]) -> None:
    """Every node and passage of these documents gone, with every link to or from them -- the
    one way a document leaves, deleted or put in again. A document that was like one has a
    place free among its best, and is measured again on the next embedding."""
    _run(
        tx, scope,
        "UNWIND $ids AS id MATCH (:Document {id: id})-[:SIMILAR_TO]-(like:Document) "
        "REMOVE like.similarity_rule",
        ids=document_ids,
    )
    # Label by label, each by its own document_id index.
    for label in (CHUNK_LABEL, NODE_LABEL):
        _run(tx, scope, f"UNWIND $ids AS id MATCH (n:{label} {{document_id: id}}) DETACH DELETE n",
             ids=document_ids)


def _readable(properties: dict[str, Any]) -> dict[str, Any]:
    """A node's properties without its centroid, which is for searching, not for reading."""
    return {k: v for k, v in properties.items() if not k.endswith("_centroid")}


def _index(record: Any, label: str) -> dict[str, Any]:
    index = {
        "kind": record["type"],
        "name": record["name"],
        "label": label,
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
       CASE WHEN n.text IS NOT NULL AND size(captions) > 0
            THEN n.text + '\n' + trim(reduce(s = '', t IN captions | s + ' ' + t))
            ELSE coalesce(n.text,
                          CASE WHEN size(captions) > 0
                               THEN trim(reduce(s = '', t IN captions | s + ' ' + t)) END,
                          CASE WHEN holds THEN n.label END, '') END AS text,
       {READING_ORDER} AS order,
       CASE WHEN n.meta_mail_quoted THEN coalesce(n.meta_mail_quote_of, '') END AS quote_of
"""
