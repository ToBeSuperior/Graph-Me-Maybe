"""New mail read as it arrives: the agent's pass over the connected accounts. Invented messages
and addresses; the mailbox is a fake and the clock is a list the test moves."""
# ruff: noqa: F811 -- the fixtures are test_mail_accounts's and test_workspaces's, used by name
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.server.jobs import Busy, CorpusBuild
from graphme.server.mail_sync import MailSync
from graphme.server.state import Demo
from graphme.server.workspaces import FIRST
from tests.fakes.documents import named_document as _doc
from tests.fakes.imap import FakeImap
from tests.public.server.conftest import until
from tests.public.server.test_mail_accounts import (  # noqa: F401
    ADDRESS,
    CONNECT,
    _raw,
    fake,
    mail,
)
from tests.public.server.test_workspaces import BASE, home, workspace_demo  # noqa: F401


def _connected(mail: TestClient, demo: Demo, address: str = ADDRESS) -> dict[str, Any]:
    """An account connected and its whole mailbox read: the first-connect question answered."""
    demo._run = _read_as_their_files  # type: ignore[method-assign]
    account = mail.post("/v1/mail/accounts", json={**CONNECT, "address": address}).json()
    job = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()
    assert until(lambda: mail.get(f"/v1/jobs/{job['id']}").json()["state"] == "done")
    return account


def _built(demo: Demo, before: str) -> bool:
    return demo.build.id != before and demo.build.state == "done"


def _held(demo: Demo, account: str) -> dict[str, Any]:
    return demo.mail_accounts().get(account)


def _read_as_their_files(session: Any) -> None:
    """A reading that takes no model: each message is a document of its own, known to the
    space by its file's content -- what a real reading puts."""
    digest = hashlib.sha256(session.source.read_bytes()).hexdigest()
    extracted = _doc(digest[:10], session.source.name)
    session.extracted = extracted.model_copy(update={"document": extracted.document.model_copy(
        update={"content_hash": digest, "source_path": session.source})})
    session.state = "done"


def test_new_mail_is_built_into_the_open_mail_workspace(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo
) -> None:
    account = _connected(mail, workspace_demo)
    before = workspace_demo.build.id
    fake.add("INBOX", _raw("later"), day=date(2026, 4, 1))

    workspace_demo.mail_sync.tick()

    assert until(lambda: _built(workspace_demo, before)), workspace_demo.build
    assert workspace_demo.build.source == ADDRESS and workspace_demo.build.total == 1
    kept = _held(workspace_demo, account["id"])
    assert kept["state"] == "current" and len(kept["pending"]) == 1  # held until it is done
    workspace_demo.mail_sync.tick()  # the next pass sees the build done
    assert _held(workspace_demo, account["id"])["pending"] == []
    assert len(list(workspace_demo.mail_accounts().messages(account["id"]).glob("*.eml"))) == 3


def test_mail_for_a_closed_workspace_waits_until_it_opens(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo
) -> None:
    account = _connected(mail, workspace_demo)
    workspace_demo.switch_workspace(FIRST)
    before = workspace_demo.build.id
    fake.add("INBOX", _raw("later"), day=date(2026, 4, 1))

    workspace_demo.mail_sync.tick()

    assert workspace_demo.build.id == before
    assert len(_held(workspace_demo, account["id"])["pending"]) == 1
    workspace_demo.switch_workspace(account["workspace"])
    assert until(lambda: _built(workspace_demo, before)), workspace_demo.build
    workspace_demo.mail_sync.tick()
    assert _held(workspace_demo, account["id"])["pending"] == []


def test_a_deleted_message_leaves_the_graph_but_a_shared_attachment_stays(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo
) -> None:
    account = _connected(mail, workspace_demo)
    files = sorted(workspace_demo.mail_accounts().messages(account["id"]).glob("*.eml"))
    old = next(f for f in files if b"<old@" in f.read_bytes())
    new = next(f for f in files if f != old)
    space = workspace_demo.graph.graph_space()
    [read] = space.read_from(str(old))  # the message as the read put it
    space.held |= {"m-old": "old.eml", "m-new": "new.eml", "shared": "q.pdf", "alone": "a.pdf"}
    space.paths |= {"m-old": str(old), "m-new": str(new)}
    carried = {"shared": {"m-old", "m-new"}, "alone": {"m-old"}}
    space.carriers = carried  # an attachment goes with the last message carrying it
    fake.folders["INBOX"].messages.pop(1)  # the old message is deleted from the mailbox

    workspace_demo.mail_sync.tick()

    assert space.deleted == [sorted(["alone", read, "m-old"])]
    assert {"m-new", "shared"} <= set(space.held) and not {"m-old", "alone"} & set(space.held)
    assert not old.exists() and new.exists()
    assert _held(workspace_demo, account["id"])["gone"] == []


def test_an_unanswered_first_connect_is_not_read(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    fake.add("INBOX", _raw("later"), day=date(2026, 4, 1))
    calls = len(fake.calls)

    workspace_demo.mail_sync.tick()

    assert len(fake.calls) == calls
    assert _held(workspace_demo, account["id"])["state"] == "counted"


def test_one_failing_account_does_not_stop_the_others_and_backs_off(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo
) -> None:
    broken = _connected(mail, workspace_demo, "lee@invented.test")
    working = _connected(mail, workspace_demo)
    accounts = workspace_demo.mail_accounts()
    accounts._vault(broken["id"]).clear()  # type: ignore[attr-defined]
    asked: list[str] = []
    real = accounts.sync
    monkey = pytest.MonkeyPatch()
    monkey.setattr(accounts, "sync", lambda a, stop=None: (asked.append(a), real(a, stop))[1])
    now = [0.0]
    sync = MailSync(accounts, workspace_demo, every=10, clock=lambda: now[0])

    def pass_at(t: float) -> list[str]:
        now[0] = t
        asked.clear()
        sync.tick()
        return list(asked)

    try:
        assert pass_at(0) == [broken["id"], working["id"]]
        assert accounts.get(broken["id"])["state"] == "error"
        assert accounts.get(working["id"])["state"] == "current"
        assert pass_at(19) == [working["id"]]  # waits twice as long
        assert pass_at(20) == [broken["id"], working["id"]]
        assert pass_at(59) == [working["id"]]  # then twice as long again
        assert pass_at(60) == [broken["id"], working["id"]]
        far = MailSync(accounts, workspace_demo, every=2000, clock=lambda: now[0])
        now[0] = 100000
        far.tick()
        assert far._retry_at[broken["id"]] == now[0] + 3600  # an hour at most
    finally:
        monkey.undo()


def test_no_pass_while_a_build_runs(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connected(mail, workspace_demo)
    asked: list[str] = []
    accounts = workspace_demo.mail_accounts()
    monkeypatch.setattr(accounts, "sync", lambda a, stop=None: asked.append(a))
    workspace_demo.build = CorpusBuild(id="running", kind="build", state="reading", source="s")

    workspace_demo.mail_sync.tick()
    assert asked == []

    workspace_demo.build = CorpusBuild(id="ended", kind="build", state="done", source="s")
    with pytest.raises(AttributeError):  # sync is stubbed to return None: the pass does ask
        workspace_demo.mail_sync.tick()
    assert len(asked) == 1


def _stub_builds(demo: Demo, monkeypatch: pytest.MonkeyPatch, *answers: Any) -> list[list[str]]:
    """`build_corpus` answers each call with the next of `answers` (a state, or Busy)."""
    asked: list[list[str]] = []
    left = list(answers)

    def build(docs: list[str], source: str, **_: Any) -> CorpusBuild:
        asked.append(docs)
        answer = left.pop(0)
        if answer is Busy:
            raise Busy("a graph build is already running")
        if answer == "done":  # what a finished build leaves: the space holds their content
            demo.graph.graph_space().hashes.update(h for d in docs if (h := demo.content_hash(d)))
        return CorpusBuild(id=f"b{len(asked)}", state=answer, source=source)

    monkeypatch.setattr(demo, "build_corpus", build)
    return asked


def test_a_build_that_fails_is_tried_again_and_a_finished_one_is_not(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = _connected(mail, workspace_demo)
    asked = _stub_builds(workspace_demo, monkeypatch, "error", "done")
    fake.add("INBOX", _raw("later"), day=date(2026, 4, 1))

    workspace_demo.mail_sync.tick()
    assert len(asked) == 1 and len(_held(workspace_demo, account["id"])["pending"]) == 1
    workspace_demo.mail_sync.tick()  # nothing new, but the failed build's file is still held
    assert asked[1] == asked[0]
    workspace_demo.mail_sync.tick()
    assert len(asked) == 2 and _held(workspace_demo, account["id"])["pending"] == []


def test_a_build_started_by_someone_else_in_the_meantime_is_not_a_failure(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = _connected(mail, workspace_demo)
    asked = _stub_builds(workspace_demo, monkeypatch, Busy, "done")
    fake.add("INBOX", _raw("later"), day=date(2026, 4, 1))

    workspace_demo.mail_sync.tick()

    kept = _held(workspace_demo, account["id"])
    assert kept["state"] == "current" and kept["error"] is None and len(kept["pending"]) == 1
    workspace_demo.mail_sync.tick()
    assert len(asked) == 2


def test_a_message_added_again_cancels_its_held_removal_and_the_reverse(
    mail: TestClient, workspace_demo: Demo
) -> None:
    account = _connected(mail, workspace_demo)["id"]
    accounts = workspace_demo.mail_accounts()
    sync = workspace_demo.mail_sync
    here, there = Path("a.eml"), Path("b.eml")
    accounts.hold(account, ["b.eml"], ["a.eml"])

    sync.hold(account, [here], [there])

    kept = accounts.get(account)
    assert kept["pending"] == ["a.eml"] and kept["gone"] == ["b.eml"]


def test_the_agents_stop_reaches_a_sync_and_ends_the_loop(
    mail: TestClient, workspace_demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    _connected(mail, workspace_demo)
    accounts = workspace_demo.mail_accounts()
    given: list[Any] = []
    real = accounts.sync
    monkeypatch.setattr(
        accounts, "sync", lambda a, stop=None: (given.append(stop), real(a, stop))[1])
    waited: list[float] = []

    def sleep(stop: threading.Event, seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            stop.set()

    stop = threading.Event()
    MailSync(accounts, workspace_demo, every=7, sleep=sleep).run(stop)

    assert waited == [7, 7] and given == [stop, stop]


def test_mail_fetched_but_never_built_is_built_by_the_next_pass(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()

    def cancelled(docs: list[str], put: bool, embed: bool) -> None:
        workspace_demo.build.state = "cancelled"  # the store is level, nothing was built

    monkeypatch.setattr(workspace_demo, "_run", _read_as_their_files)
    real = workspace_demo._build_corpus
    monkeypatch.setattr(workspace_demo, "_build_corpus", cancelled)
    job = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()
    assert until(lambda: mail.get(f"/v1/jobs/{job['id']}").json()["state"] == "cancelled")
    assert _held(workspace_demo, account["id"])["state"] == "current"
    monkeypatch.setattr(workspace_demo, "_build_corpus", real)
    before = workspace_demo.build.id

    workspace_demo.mail_sync.tick()  # nothing new in the mailbox: what the store holds is built

    assert until(lambda: _built(workspace_demo, before)), workspace_demo.build
    assert workspace_demo.build.documents == 2
    done = workspace_demo.build.id
    workspace_demo.mail_sync.tick()
    workspace_demo.mail_sync.tick()
    assert workspace_demo.build.id == done  # everything is in: nothing is built again


def test_a_message_deleted_before_a_read_leaves_the_graph(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo
) -> None:
    account = _connected(mail, workspace_demo)
    files = sorted(workspace_demo.mail_accounts().messages(account["id"]).glob("*.eml"))
    old = next(f for f in files if b"<old@" in f.read_bytes())
    space = workspace_demo.graph.graph_space()
    [read] = space.read_from(str(old))
    fake.folders["INBOX"].messages.pop(1)  # deleted from the mailbox, then read earlier mail

    job = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()
    assert until(lambda: mail.get(f"/v1/jobs/{job['id']}").json()["state"] == "done")
    workspace_demo.mail_sync.tick()

    assert [read] in space.deleted and read not in space.held


def test_the_first_pass_comes_before_the_first_wait(
    mail: TestClient, workspace_demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    _connected(mail, workspace_demo)
    accounts = workspace_demo.mail_accounts()
    order: list[str] = []
    real = accounts.sync
    monkeypatch.setattr(
        accounts, "sync", lambda a, stop=None: (order.append("pass"), real(a, stop))[1])

    def sleep(stop: threading.Event, seconds: float) -> None:
        order.append("wait")
        stop.set()

    MailSync(accounts, workspace_demo, every=7, sleep=sleep).run(threading.Event())

    assert order == ["pass", "wait"]
