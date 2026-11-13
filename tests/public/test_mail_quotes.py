"""A reply's quoted history is a unit of its own, and is left out of answers once the message
it quotes is in the space. Invented messages."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Any

from graphme.adapters.extractors.mail import MailExtractor
from graphme.adapters.storage.hybrid_search import (
    _OnceRetriever,
    documents_with_message_ids,
    linked_without_answered_quotes,
    without_answered_quotes,
)
from graphme.application.graph_chunks import GraphUnit, chunks
from graphme.domain.enums import NodeType

ORIGINAL = "<a@invented.test>"


def _reply(tmp_path: Path, plain: str | None = None, html: str | None = None) -> Path:
    message = EmailMessage()
    message["From"] = "Lee <lee@invented.test>"
    message["Message-ID"] = "<b@invented.test>"
    message["In-Reply-To"] = ORIGINAL
    if plain is not None:
        message.set_content(plain)
    if html is not None:
        message.add_alternative(html, subtype="html") if plain else message.set_content(
            html, subtype="html")
    path = tmp_path / "r.eml"
    path.write_bytes(message.as_bytes())
    return path


def _units(path: Path) -> list[tuple[str, bool, Any]]:
    return [(n.text or "", bool(n.metadata.get("mail_quoted")), n.metadata.get("mail_quote_of"))
            for n in MailExtractor().extract(path).nodes if n.type is not NodeType.DOCUMENT]


OWN = "네, 확인했습니다."
QUOTED = "단가 1,200원입니다."


def _check(units: list[tuple[str, bool, Any]]) -> None:
    [(own, own_quoted, _)] = [u for u in units if not u[1]]
    [(quote, _, quote_of)] = [u for u in units if u[1]]
    assert (own, own_quoted) == (OWN, False)
    assert QUOTED in quote and OWN not in quote
    assert quote_of == ORIGINAL


def test_a_replys_quote_is_its_own_unit(tmp_path: Path) -> None:
    _check(_units(_reply(tmp_path, f"{OWN}\n\nOn Mon, Kim wrote:\n> {QUOTED}")))


def test_an_outlook_block_is_quoted_to_its_end(tmp_path: Path) -> None:
    body = (f"{OWN}\n\n-----Original Message-----\nFrom: Kim\nSent: Monday\nTo: Lee\n\n{QUOTED}")
    _check(_units(_reply(tmp_path, body)))
    body = f"{OWN}\n\nFrom: Kim\nSent: Monday\nTo: Lee\n\n{QUOTED}"
    _check(_units(_reply(tmp_path, body)))


def test_a_korean_attribution_starts_the_quote(tmp_path: Path) -> None:
    _check(_units(_reply(tmp_path, f"{OWN}\n\n2026년 10월 12일 김 작성:\n{QUOTED}")))


def test_a_from_line_without_sent_is_own_text(tmp_path: Path) -> None:
    units = _units(_reply(tmp_path, "From: the warehouse\nit is shipped"))
    assert all(not quoted for _, quoted, _ in units)


def test_a_gmail_quote_is_quoted_html(tmp_path: Path) -> None:
    html = (f"<div>{OWN}</div><div class='gmail_quote'><div class='gmail_attr'>On Mon, Kim "
            f"wrote:</div><blockquote><p>{QUOTED}</p></blockquote></div>")
    _check(_units(_reply(tmp_path, html=html)))
    _check(_units(_reply(tmp_path, html=f"<p>{OWN}</p><blockquote>{QUOTED}</blockquote>")))


def test_an_outlook_html_block_is_quoted_to_its_end(tmp_path: Path) -> None:
    html = (f"<p>{OWN}</p><hr><div id='divRplyFwdMsg'><b>From:</b> Kim<br><b>Sent:</b> Monday"
            f"</div><div><p>{QUOTED}</p></div>")
    _check(_units(_reply(tmp_path, html=html)))


def test_a_quote_is_never_in_a_passage_with_own_text(tmp_path: Path) -> None:
    graph = MailExtractor().extract(_reply(tmp_path, f"{OWN}\n\nOn Mon, Kim wrote:\n> {QUOTED}"))
    units = [GraphUnit(id=n.id, parent=None, text=n.text or "", order=(i,),
                       quote_of=n.metadata.get("mail_quote_of") if n.metadata.get("mail_quoted")
                       else None)
             for i, n in enumerate(n for n in graph.nodes if n.type is not NodeType.DOCUMENT)]

    cut = chunks("r.eml", units)

    assert [c.quote_of for c in cut] == [None, ORIGINAL]
    assert OWN in cut[0].text and QUOTED not in cut[0].text
    assert QUOTED in cut[1].text and OWN not in cut[1].text


def _found(*rows: tuple[str, str | None]) -> list[dict[str, Any]]:
    return [{"id": f"{doc}:0", "document_id": doc, "quote_of": quote} for doc, quote in rows]


def test_a_quote_is_dropped_once_its_message_is_in() -> None:
    found = _found(("reply", ORIGINAL), ("original", None))

    kept = without_answered_quotes(found, in_space={ORIGINAL})

    assert [r["document_id"] for r in kept] == ["original"]


def test_a_quote_whose_message_is_not_in_is_kept() -> None:
    found = _found(("reply", ORIGINAL), ("original", None), ("other", ""))

    kept = without_answered_quotes(found, in_space=set())

    assert [r["document_id"] for r in kept] == ["reply", "original", "other"]


def test_a_wrapped_attribution_starts_the_quote(tmp_path: Path) -> None:
    on = "On Mon, Oct 12, 2026 at 3:00 PM Kim <kim@invented.test>"
    _check(_units(_reply(tmp_path, f"{OWN}\n\n{on}\nwrote:\n> {QUOTED}")))
    ko = "2026년 10월 12일 오후 3:00 김 <kim@invented.test>"
    _check(_units(_reply(tmp_path, f"{OWN}\n\n{ko}\n작성:\n{QUOTED}")))


def test_a_linked_documents_quote_is_dropped_too() -> None:
    quote = {"id": "q", "quote_of": ORIGINAL}
    linked = [{"document_id": "reply", "passages": [quote]},
              {"document_id": "mixed", "passages": [quote, {"id": "own", "quote_of": None}]}]

    kept = linked_without_answered_quotes(linked, in_space={ORIGINAL})

    assert [(d["document_id"], [p["id"] for p in d["passages"]]) for d in kept] == [
        ("mixed", ["own"])]


class _Driver:
    """Answers the search with `rows`, and the Message-ID lookup from `held`."""

    def __init__(self, rows: list[dict[str, Any]], held: set[str]) -> None:
        self.rows, self.held, self.calls = rows, held, []

    def execute_query(self, query: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        self.calls.append((query, params))
        if "meta_mail_message_id" in query:
            ids = (params or {})["ids"]
            return [{"id": i} for i in ids if i in self.held], None, None
        return self.rows, None, None


def test_the_message_id_lookup_asks_for_the_ids_given() -> None:
    driver = _Driver([], {ORIGINAL})

    assert documents_with_message_ids(driver, [ORIGINAL, "<z@invented.test>"]) == {ORIGINAL}  # type: ignore[arg-type]
    assert driver.calls[0][1] == {"ids": [ORIGINAL, "<z@invented.test>"]}
    assert documents_with_message_ids(driver, []) == set()  # type: ignore[arg-type]
    assert len(driver.calls) == 1


def _fused(held: set[str]) -> list[str]:
    rows = [{"id": "reply:0", "score": 1.0, "quote_of": ORIGINAL},
            {"id": "original:0", "score": 0.9, "quote_of": None}]
    retriever = object.__new__(_OnceRetriever)
    retriever.driver = _Driver(rows, held)  # type: ignore[assignment]
    retriever.text_only = True
    retriever.retrieval_query = ""
    retriever.neo4j_database = None
    retriever.dense_weight, retriever.floor, retriever.rrf_k = 0.5, 0.0, 60
    retriever.vector_index_name = "vec"
    retriever.fulltext_index_name = "ft"
    return [r["id"] for r in retriever.fused("단가가 얼마입니까")]


def test_fused_drops_a_quote_once_its_message_is_in() -> None:
    assert _fused({ORIGINAL}) == ["original:0"]


def test_fused_keeps_a_quote_whose_message_is_not_in() -> None:
    assert _fused(set()) == ["reply:0", "original:0"]


def test_the_links_ask_the_workspaces_own_database() -> None:
    import neo4j

    from graphme.adapters.storage.graph_rag import GraphRag

    linked = [{"document_id": "original", "passages": [
        {"id": "original:0", "score": 1.0, "quote_of": ORIGINAL}]}]
    driver = _Driver([{"document_id": "reply", "linked": linked}], {ORIGINAL})
    asked: list[Any] = []
    real = driver.execute_query

    def execute_query(query: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        asked.append(kw.get("database_"))
        return real(query, params, **kw)

    driver.execute_query = execute_query  # type: ignore[method-assign]
    retriever = object.__new__(_OnceRetriever)
    retriever.text_only, retriever.neo4j_database = False, "workspace"
    retriever.embedder = type("E", (), {"embed_query": lambda self, q: [0.0]})()  # type: ignore[assignment]
    rag = object.__new__(GraphRag)
    rag._space = type("S", (), {"driver": driver})()  # type: ignore[attr-defined]
    rag._floor = 0.0  # type: ignore[attr-defined]
    rag._retriever = lambda: retriever  # type: ignore[method-assign]
    rag._links_query = lambda: "LINKS"  # type: ignore[method-assign]

    found = [neo4j.Record({"document_id": "reply", "id": "reply:0"})]  # type: ignore[no-untyped-call]
    rag._linked(found, "단가")  # type: ignore[attr-defined]

    assert asked == ["workspace", "workspace"]
