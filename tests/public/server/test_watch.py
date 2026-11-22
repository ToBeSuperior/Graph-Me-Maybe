"""Automatic builds on the targets the user chose: folders, drive folders and mail accounts.
Invented files in a temporary folder; the clock is a list the test moves."""
# ruff: noqa: F811 -- the fixtures are test_mail_accounts's and test_workspaces's, used by name
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.adapters.sources.desktop_drives import Drive
from graphme.server import create_app, watch
from graphme.server.jobs import Busy, CorpusBuild
from graphme.server.mail_sync import MailSync
from graphme.server.state import Demo
from graphme.server.watch import SETTLE_MINUTES, Watcher, WatchTarget, next_daily
from graphme.server.workspaces import FIRST
from tests.fakes.imap import FakeImap
from tests.public.server.conftest import until
from tests.public.server.test_mail_accounts import (  # noqa: F401
    ADDRESS,
    _raw,
    fake,
    mail,
)
from tests.public.server.test_mail_sync import _connected, _read_as_their_files
from tests.public.server.test_workspaces import BASE, home, workspace_demo  # noqa: F401

START = datetime(2026, 5, 4, 1, 0)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def after(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def watched(workspace_demo: Demo, clock: Clock) -> tuple[TestClient, Watcher]:
    workspace_demo._run = _read_as_their_files  # type: ignore[method-assign]
    watcher = Watcher(workspace_demo, now=clock)
    workspace_demo._watcher = watcher
    return TestClient(create_app(workspace_demo), base_url=BASE), watcher


def _folder(demo: Demo, tmp_path: Path, name: str = "reports") -> Path:
    """A folder the open workspace was built from: its plan is in the graph."""
    folder = tmp_path / name
    folder.mkdir(parents=True)
    _in_graph(demo, folder / "plan.docx")
    return folder


def _in_graph(demo: Demo, path: Path, content: bytes = b"an invented plan") -> str:
    path.write_bytes(content)
    space = demo.space()
    doc = f"d-{path.parent.name}-{path.name}"
    space.paths[doc] = str(path)  # type: ignore[attr-defined]
    space.held[doc] = path.name  # type: ignore[attr-defined]
    return doc


def _choose(client: TestClient, folder: Path, mode: str, at: str | None = None,
            kind: str = "folder") -> dict[str, Any]:
    said = client.put(f"/v1/watch/{kind}/t1",
                      json={"mode": mode, "at": at, "path": str(folder)})
    assert said.status_code == 200, said.text
    return dict(said.json())


def _target(client: TestClient, id: str = "t1") -> dict[str, Any]:
    return dict(next(t for t in client.get("/v1/watch").json() if t["id"] == id))


def _built(demo: Demo, before: str) -> bool:
    return demo.build.id != before and demo.build.state == "done"


def test_a_manual_target_counts_what_changed_and_never_builds(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "manual")
    before = workspace_demo.build.id
    (folder / "price.xlsx").write_bytes(b"an invented price list")
    (folder / "plan.docx").write_bytes(b"an invented plan, changed")

    for _ in range(4):
        watcher.tick()
        clock.after(hours=6)

    said = _target(client)
    assert said["changed"] == 2 and said["mode"] == "manual" and said["next_build"] is None
    assert workspace_demo.build.id == before


def test_on_change_builds_once_after_it_settles(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "on_change")
    before = workspace_demo.build.id
    (folder / "price.xlsx").write_bytes(b"an invented price list")

    watcher.tick()  # the change is seen; not quiet long enough yet
    assert workspace_demo.build.id == before
    assert _target(client)["changed"] == 1
    clock.after(minutes=SETTLE_MINUTES)
    watcher.tick()

    assert until(lambda: _built(workspace_demo, before)), workspace_demo.build
    assert workspace_demo.build.total == 1  # the changed file only
    built = workspace_demo.build.id
    clock.after(minutes=SETTLE_MINUTES)
    watcher.tick()  # the build is taken: nothing changed since
    clock.after(minutes=SETTLE_MINUTES)
    watcher.tick()
    assert workspace_demo.build.id == built
    assert _target(client)["changed"] == 0


def test_daily_builds_at_its_time(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "daily", at="02:00")
    before = workspace_demo.build.id
    (folder / "price.xlsx").write_bytes(b"an invented price list")

    clock.after(minutes=30)
    watcher.tick()
    assert workspace_demo.build.id == before
    assert _target(client)["next_build"] == "2026-05-04T02:00:00"
    clock.after(minutes=30)
    watcher.tick()

    assert until(lambda: _built(workspace_demo, before)), workspace_demo.build
    watcher.tick()
    assert _target(client)["next_build"] == "2026-05-05T02:00:00"


def test_an_unchosen_folder_is_never_looked_at(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, watcher = watched
    chosen, other = _folder(workspace_demo, tmp_path), _folder(workspace_demo, tmp_path, "private")
    looked: list[Path] = []
    walk = watch.documents_at
    monkeypatch.setattr(watch, "documents_at",
                        lambda path, **kw: looked.append(path) or walk(path, **kw))
    _choose(client, chosen, "on_change")

    watcher.tick()

    assert looked and all(path == chosen for path in looked)
    assert all(t.get("path") != str(other) for t in client.get("/v1/watch").json())


def test_a_removed_file_is_listed_missing_and_stays_in_the_graph(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "on_change")
    space = workspace_demo.space()
    (folder / "plan.docx").unlink()

    for _ in range(3):
        watcher.tick()
        clock.after(minutes=SETTLE_MINUTES)

    said = _target(client)
    assert said["missing"] == [{"path": str(folder / "plan.docx"),
                                "document_id": "d-reports-plan.docx"}]
    assert said["changed"] == 0
    assert space.deleted == []  # type: ignore[attr-defined]


def test_a_drive_target_must_be_in_a_connected_drive(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = watched
    base = tmp_path / "My Drive"
    folder = _folder(workspace_demo, base, "shared")
    monkeypatch.setattr(type(workspace_demo), "desktop_drive",
                        staticmethod(lambda kind: Drive(base) if kind == "google" else None))

    refused = client.put("/v1/watch/drive/t1", json={"mode": "daily", "at": "03:00",
                                                     "path": str(folder)})
    workspace_demo.drive_connect("google", True)
    taken = _choose(client, folder, "daily", at="03:00", kind="drive")

    assert refused.status_code == 400
    assert taken["kind"] == "drive" and taken["mode"] == "daily"


def test_a_daily_target_needs_its_time(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, tmp_path: Path
) -> None:
    client, _ = watched
    folder = _folder(workspace_demo, tmp_path)
    said = client.put("/v1/watch/folder/t1", json={"mode": "daily", "path": str(folder)})
    assert said.status_code == 400


def test_a_new_mail_account_waits_to_be_asked(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo
) -> None:
    account = _connected(mail, workspace_demo, automatic=False)
    before = workspace_demo.build.id
    fake.add("INBOX", _raw("later"), day=date(2026, 4, 1))

    workspace_demo.mail_sync.tick()

    said = next(t for t in mail.get("/v1/watch").json() if t["id"] == account["id"])
    assert said["kind"] == "mail" and said["mode"] == "manual"
    assert workspace_demo.build.id == before
    assert len(list(workspace_demo.mail_accounts().messages(account["id"]).glob("*.eml"))) == 2


def test_an_account_from_before_keeps_fetching_by_itself(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo, home: Path
) -> None:
    account = _connected(mail, workspace_demo, automatic=False)
    kept = home / "mail_accounts.json"
    said = json.loads(kept.read_text(encoding="utf-8"))
    for item in said["accounts"]:
        item.pop("mode", None)  # written before an account had a mode
    kept.write_text(json.dumps(said), encoding="utf-8")
    before = workspace_demo.build.id
    fake.add("INBOX", _raw("later"), day=date(2026, 4, 1))

    workspace_demo.mail_sync.tick()

    assert until(lambda: _built(workspace_demo, before)), workspace_demo.build
    listed = next(t for t in mail.get("/v1/watch").json() if t["id"] == account["id"])
    assert listed["mode"] == "on_change"


def test_a_chosen_folder_is_kept_across_starts(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path
) -> None:
    client, _ = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "manual")
    os.utime(folder / "plan.docx", (1, 1))  # touched: a changed time is a change

    again = Watcher(workspace_demo, now=clock)
    again.tick()

    assert [(t["path"], t["changed"]) for t in again.targets() if t["kind"] == "folder"] \
        == [(str(folder), 1)]


def _settled(watcher: Watcher, clock: Clock) -> None:
    """A look, then another once the change has been quiet long enough."""
    watcher.tick()
    clock.after(minutes=SETTLE_MINUTES)
    watcher.tick()


def test_a_switch_between_the_check_and_the_build_builds_nothing(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "on_change")
    spaces = workspace_demo.workspace_registry()
    other = spaces.create("Invented other")["id"]
    real = workspace_demo.build_corpus

    def switched_first(*args: Any, **kwargs: Any) -> CorpusBuild:
        spaces.activate(other)  # a switch finished between the watcher's check and the build
        return real(*args, **kwargs)

    monkeypatch.setattr(workspace_demo, "build_corpus", switched_first)
    before = workspace_demo.build.id
    (folder / "price.xlsx").write_bytes(b"an invented price list")

    _settled(watcher, clock)

    assert workspace_demo.build.id == before
    assert _target(client)["changed"] == 1  # still waiting for its workspace


def test_a_watched_build_never_goes_into_a_mail_workspace(workspace_demo: Demo) -> None:
    spaces = workspace_demo.workspace_registry()
    mailbox = spaces.create("kim@invented.test", kind="mail", account="a1")["id"]
    spaces.activate(mailbox)

    with pytest.raises(Busy):
        workspace_demo.build_corpus([], "a folder", into=mailbox)


def test_a_cancelled_build_waits_for_a_new_change(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "on_change")
    real = workspace_demo._build_corpus

    def cancelled(docs: list[str], put: bool, embed: bool) -> None:
        workspace_demo.build.state = "cancelled"

    monkeypatch.setattr(workspace_demo, "_build_corpus", cancelled)
    (folder / "price.xlsx").write_bytes(b"an invented price list")
    _settled(watcher, clock)
    assert until(lambda: workspace_demo.build.state == "cancelled")
    first = workspace_demo.build.id

    for _ in range(3):
        clock.after(minutes=SETTLE_MINUTES)
        watcher.tick()
    assert workspace_demo.build.id == first

    monkeypatch.setattr(workspace_demo, "_build_corpus", real)
    (folder / "terms.pdf").write_bytes(b"invented terms")
    _settled(watcher, clock)
    assert until(lambda: _built(workspace_demo, first)), workspace_demo.build


@pytest.mark.parametrize(("why", "said_why"), [
    ("root", "whole drive"), ("home", "home folder"), ("system", "system folder"),
    ("users", "system folder"),
    ("app home", "app's own"), ("inside", "chosen already"), ("around", "chosen already"),
    ("not built", "built from"),
])
def test_a_folder_target_is_refused(
    why: str, said_why: str, watched: tuple[TestClient, Watcher], workspace_demo: Demo,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, watcher = watched
    user = tmp_path / "user"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    monkeypatch.setattr(watch, "home_of", lambda settings: user / "app")
    reports = _folder(workspace_demo, user / "docs", "reports")
    nested = reports / "2026"
    nested.mkdir()
    _in_graph(workspace_demo, nested / "minutes.docx")
    if why == "inside":
        _choose(client, reports, "manual")
    if why == "around":  # a target inside the one asked for, chosen before it was built whole
        watcher._write([WatchTarget("folder", "t1", FIRST, path=str(nested))])
    asked = {
        "root": Path(Path.cwd().anchor),
        "home": user,
        "system": Path("/usr") if Path("/usr").is_dir() else Path("C:/Windows"),
        "users": Path("/Users") if Path("/Users").is_dir() else Path("/home"),
        "app home": _folder(workspace_demo, user / "app", "workspaces"),
        "inside": nested,
        "around": reports,
        "not built": tmp_path / "elsewhere",
    }[why]
    asked.mkdir(exist_ok=True)

    said = client.put("/v1/watch/folder/t2", json={"mode": "manual", "path": str(asked)})

    assert said.status_code == 400 and said_why in said.json()["detail"], said.text


def test_the_folders_offered_are_those_the_workspace_was_built_from(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, tmp_path: Path
) -> None:
    client, _ = watched
    reports = _folder(workspace_demo, tmp_path / "work")
    (reports / "2026").mkdir()
    _in_graph(workspace_demo, reports / "2026" / "minutes.docx")
    _folder(workspace_demo, tmp_path / "work", "contracts")

    said = client.get("/v1/watch/folders").json()

    # Each document's own folder, nested ones too; "work" holds none itself.
    assert said == [{"path": str(tmp_path / "work" / "contracts"), "kind": "folder"},
                    {"path": str(reports), "kind": "folder"},
                    {"path": str(reports / "2026"), "kind": "folder"}]


def test_a_folder_that_would_be_refused_is_not_offered(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = watched
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    _in_graph(workspace_demo, user / "notes.docx")  # built from the home folder itself
    docs = _folder(workspace_demo, user, "docs")

    said = client.get("/v1/watch/folders").json()

    assert said == [{"path": str(docs), "kind": "folder"}]


def test_a_folder_built_through_a_link_can_be_chosen(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, tmp_path: Path
) -> None:
    client, _ = watched
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    (link / "reports").mkdir()
    _in_graph(workspace_demo, link / "reports" / "plan.docx")  # the graph keeps the link's path

    offered = client.get("/v1/watch/folders").json()
    said = client.put("/v1/watch/folder/t1",
                      json={"mode": "manual", "path": str(link / "reports")})

    assert offered == [{"path": str(real / "reports"), "kind": "folder"}]
    assert said.status_code == 200, said.text


def test_a_folder_back_from_away_settles_again_before_it_builds(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "on_change")
    (folder / "price.xlsx").write_bytes(b"an invented price list")
    watcher.tick()  # the change is seen
    hidden = tmp_path / "away"
    folder.rename(hidden)
    clock.after(minutes=SETTLE_MINUTES * 2)
    watcher.tick()
    hidden.rename(folder)
    before = workspace_demo.build.id

    watcher.tick()  # back: what it holds is looked at afresh, not taken as long quiet
    assert workspace_demo.build.id == before
    clock.after(minutes=SETTLE_MINUTES)
    watcher.tick()

    assert until(lambda: _built(workspace_demo, before)), workspace_demo.build


def test_a_folder_that_vanished_is_said_and_builds_nothing_until_it_is_back(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "on_change")
    hidden = tmp_path / "away"
    folder.rename(hidden)
    before = workspace_demo.build.id

    _settled(watcher, clock)

    said = _target(client)
    assert said["error"] and workspace_demo.build.id == before
    hidden.rename(folder)
    watcher.tick()
    assert _target(client)["error"] is None


def test_what_a_walk_passed_over_is_not_missing(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    late = folder / "late"
    late.mkdir()
    _in_graph(workspace_demo, late / "minutes.docx")
    _choose(client, folder, "manual")
    walk = watch.documents_at

    def timed_out(path: Path, *, skipped: list[Path], **kw: Any) -> list[Path]:
        skipped.append(late)  # the drive did not list it in time
        return [p for p in walk(path, **kw) if late not in p.parents]

    monkeypatch.setattr(watch, "documents_at", timed_out)
    watcher.tick()

    assert _target(client)["missing"] == []


def test_build_now_says_why_it_cannot(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, tmp_path: Path
) -> None:
    client, _ = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "manual")
    spaces = workspace_demo.workspace_registry()

    nothing = client.post("/v1/watch/folder/t1/build")
    wrong_kind = client.post("/v1/watch/drive/t1/build")
    (folder / "price.xlsx").write_bytes(b"an invented price list")
    workspace_demo.build = CorpusBuild(id="b0", state="reading")
    running = client.post("/v1/watch/folder/t1/build")
    workspace_demo.build = CorpusBuild()
    spaces.activate(spaces.create("Invented other")["id"])
    closed = client.post("/v1/watch/folder/t1/build")

    assert nothing.status_code == 409 and "nothing changed" in nothing.text
    assert wrong_kind.status_code == 404
    assert running.status_code == 409 and "already running" in running.text
    assert closed.status_code == 409 and "not open" in closed.text
    assert client.delete("/v1/watch/drive/t1").status_code == 404


def test_a_daily_target_of_a_closed_workspace_shows_its_next_time(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path
) -> None:
    client, watcher = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "daily", at="02:00")
    (folder / "price.xlsx").write_bytes(b"an invented price list")
    spaces = workspace_demo.workspace_registry()
    spaces.activate(spaces.create("Invented other")["id"])
    clock.after(hours=3)

    watcher.tick()

    assert _target(client)["next_build"] == "2026-05-05T02:00:00"


def test_a_daily_time_missed_while_closed_builds_at_the_next_start(
    watched: tuple[TestClient, Watcher], workspace_demo: Demo, clock: Clock, tmp_path: Path
) -> None:
    client, _ = watched
    folder = _folder(workspace_demo, tmp_path)
    _choose(client, folder, "daily", at="02:00")
    (folder / "price.xlsx").write_bytes(b"an invented price list")
    before = workspace_demo.build.id
    clock.after(days=1, hours=8)  # the app was closed through the night

    Watcher(workspace_demo, now=clock).tick()

    assert until(lambda: _built(workspace_demo, before)), workspace_demo.build


def test_a_daily_time_is_the_clock_time_on_a_day_the_clocks_change() -> None:
    assert next_daily("02:30", datetime(2026, 3, 8, 1, 0)) == datetime(2026, 3, 8, 2, 30)
    assert next_daily("02:30", datetime(2026, 3, 8, 3, 0)) == datetime(2026, 3, 9, 2, 30)
    assert next_daily("01:30", datetime(2026, 11, 1, 1, 45)) == datetime(2026, 11, 2, 1, 30)


def test_a_daily_mail_account_fetches_once_its_time_has_come(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo, clock: Clock
) -> None:
    account = _connected(mail, workspace_demo, automatic=False)
    accounts = workspace_demo.mail_accounts()
    assert mail.put(f"/v1/watch/mail/{account['id']}",
                    json={"mode": "daily", "at": "06:00"}).status_code == 200
    accounts._change(account["id"], last_checked="2026-05-03T07:00:00")
    workspace_demo._mail_sync = MailSync(accounts, workspace_demo, now=clock)
    fake.add("INBOX", _raw("later"), day=date(2026, 4, 1))
    before = workspace_demo.build.id

    clock.now = datetime(2026, 5, 4, 5, 0)
    workspace_demo.mail_sync.tick()
    assert workspace_demo.build.id == before
    clock.now = datetime(2026, 5, 4, 6, 0)
    workspace_demo.mail_sync.tick()

    assert until(lambda: _built(workspace_demo, before)), workspace_demo.build


def test_a_mail_account_says_when_it_is_building(
    mail: TestClient, workspace_demo: Demo
) -> None:
    account = _connected(mail, workspace_demo, automatic=False)
    workspace_demo.build = CorpusBuild(id="b1", state="reading", source=ADDRESS)

    said = next(t for t in mail.get("/v1/watch").json() if t["id"] == account["id"])

    assert said["building"] is True
