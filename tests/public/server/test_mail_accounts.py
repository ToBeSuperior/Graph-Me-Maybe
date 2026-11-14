"""Mail accounts through the server: each connected into a workspace of its own, counted before
anything is read, its password only in the vault. Invented messages and addresses."""
# ruff: noqa: F811 -- the workspace fixtures are test_workspaces's, used by name
from __future__ import annotations

import json
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.adapters.sources.sign_in import FileVault
from graphme.server import create_app
from graphme.server.jobs import CorpusBuild
from graphme.server.mail_accounts import MailAccounts
from graphme.server.state import Demo
from graphme.server.workspaces import FIRST, Workspaces
from tests.fakes.imap import FakeFolder, FakeImap
from tests.public.server.conftest import read_as, until
from tests.public.server.test_workspaces import BASE, home, workspace_demo  # noqa: F401

PASSWORD = "invented-app-password"
ADDRESS = "kim@invented.test"
CONNECT = {"host": "imap.invented.test", "port": 993, "address": ADDRESS, "password": PASSWORD}


def _raw(identifier: str) -> bytes:
    message = EmailMessage()
    message["From"] = "Lee <lee@invented.test>"
    message["Subject"] = f"견적 {identifier}"
    message["Message-ID"] = f"<{identifier}@invented.test>"
    message.set_content("본문입니다.")
    return message.as_bytes()


@pytest.fixture
def fake() -> FakeImap:
    imap = FakeImap({"INBOX": FakeFolder()})
    imap.add("INBOX", _raw("old"), day=date(2025, 1, 5))
    imap.add("INBOX", _raw("new"), day=date(2026, 3, 1))
    return imap


@pytest.fixture
def mail(workspace_demo: Demo, fake: FakeImap, home: Path,
         monkeypatch: pytest.MonkeyPatch) -> TestClient:
    spaces = workspace_demo.workspace_registry()
    workspace_demo._mail = MailAccounts(  # type: ignore[attr-defined]
        home, spaces, lambda fallback, account: FileVault(fallback), connect=fake)
    workspace_demo._run = read_as("a", "m.eml")  # type: ignore[method-assign]
    return TestClient(create_app(workspace_demo), base_url=BASE)


def test_connecting_makes_a_mail_workspace_and_counts_without_reading(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo
) -> None:
    made = mail.post("/v1/mail/accounts", json=CONNECT)

    assert made.status_code == 200
    account = made.json()
    assert account["state"] == "counted" and account["count"]["messages"] == 2
    assert account["count"]["oldest"] == "2025-01-05"
    workspaces = mail.get("/v1/workspaces").json()
    assert {"id": account["workspace"], "name": ADDRESS, "mcp": False, "kind": "mail",
            "account": account["id"]} in workspaces["items"]
    assert workspaces["active"] == FIRST  # made, not opened
    assert not any(call[:2] == ("uid", "FETCH") and "BODY.PEEK[]" in call[3:]
                   for call in fake.calls)
    assert mail.get("/v1/mail/accounts").json() == [account]


def test_the_password_is_never_answered_or_written_to_env(
    mail: TestClient, home: Path, tmp_path: Path
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    job = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()
    # Done before the files are looked through: a message still being written is a temp file
    # that is gone by the time it is read.
    assert until(lambda: mail.get(f"/v1/jobs/{job['id']}").json()["state"] == "done")
    answers = [mail.get("/v1/mail/accounts").text, mail.get("/v1/workspaces").text, str(account)]

    assert all(PASSWORD not in each for each in answers)
    holders = [p for p in tmp_path.rglob("*")
               if p.is_file() and PASSWORD.encode() in p.read_bytes()]
    assert holders == [home / ".graphme" / "mail" / account["id"]]
    # only its owner reads it (Windows keeps a profile's files to their owner by ACL, not mode)
    assert sys.platform == "win32" or holders[0].stat().st_mode & 0o077 == 0


def test_a_refused_login_says_why_and_makes_nothing(
    mail: TestClient, fake: FakeImap, home: Path
) -> None:
    fake.login_error = "[AUTHENTICATIONFAILED] invalid credentials"

    refused = mail.post("/v1/mail/accounts", json=CONNECT)

    assert refused.status_code == 400
    assert "AUTHENTICATIONFAILED" in refused.json()["detail"]
    assert PASSWORD not in refused.text
    assert mail.get("/v1/mail/accounts").json() == []
    assert [i["id"] for i in mail.get("/v1/workspaces").json()["items"]] == [FIRST]
    assert not (home / ".graphme").exists()


def _bodies(fake: FakeImap) -> list[str]:
    """The UIDs whose message was downloaded, in order."""
    return [c[2] for c in fake.calls if c[:2] == ("uid", "FETCH") and "BODY.PEEK[]" in c[3]]


def test_reading_since_a_date_downloads_nothing_older(
    mail: TestClient, fake: FakeImap
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    job = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": "2026-01-01"})
    assert until(lambda: mail.get(f"/v1/jobs/{job.json()['id']}").json()["state"] == "done")
    assert _bodies(fake) == ["2"]  # the 2025 message stays on the server, not downloaded


def test_a_read_cancelled_half_way_leaves_the_account_to_read_on(
    mail: TestClient, fake: FakeImap, workspace_demo: Demo
) -> None:
    fake.add("INBOX", _raw("third"), day=date(2026, 4, 1))
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    real = fake.uid

    def cancelling(command: str, *args: Any) -> tuple[str, list[Any]]:
        answer = real(command, *args)
        if fake.fetched == 1:
            workspace_demo.cancel_build()  # the user's Cancel, after one message came
        return answer

    fake.uid = cancelling  # type: ignore[method-assign]
    job = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()
    assert until(lambda: mail.get(f"/v1/jobs/{job['id']}").json()["state"] == "cancelled")
    fake.uid = real  # type: ignore[method-assign]

    accounts = workspace_demo.mail_accounts()
    kept = accounts.get(account["id"])
    assert kept["state"] == "counted" and kept["error"] is None  # not left "reading"
    files = list(accounts.messages(account["id"]).glob("*.eml"))
    assert len(files) == 1 and accounts.store(account["id"]).state("INBOX").uids == {3}
    fake.calls.clear()
    again = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()
    assert until(lambda: mail.get(f"/v1/jobs/{again['id']}").json()["state"] == "done")
    assert _bodies(fake) == ["2", "1"]  # on from where it stopped, nothing fetched twice
    assert accounts.get(account["id"])["state"] == "current"
    assert len(list(accounts.messages(account["id"]).glob("*.eml"))) == 3


def test_reading_since_a_date_then_earlier(
    mail: TestClient, workspace_demo: Demo, home: Path
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    messages = home / "workspaces" / account["workspace"] / "mail" / "messages"

    first = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": "2026-01-01"})
    assert first.status_code == 202, first.text
    assert until(lambda: mail.get(f"/v1/jobs/{first.json()['id']}").json()["state"] == "done")

    assert mail.get("/v1/workspaces").json()["active"] == account["workspace"]
    assert len(list(messages.glob("*.eml"))) == 1
    kept = mail.get("/v1/mail/accounts").json()[0]
    assert kept["state"] == "current" and kept["read_since"] == "2026-01-01"

    second = mail.post(f"/v1/mail/accounts/{account['id']}/earlier")
    assert second.status_code == 202, second.text
    assert until(lambda: mail.get(f"/v1/jobs/{second.json()['id']}").json()["state"] == "done")
    assert len(list(messages.glob("*.eml"))) == 2
    assert mail.get("/v1/mail/accounts").json()[0]["read_since"] is None


def test_a_mailbox_that_drops_says_so_in_the_account_and_the_job(
    mail: TestClient, fake: FakeImap
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    fake.fail_fetch_after = 0

    job = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()

    assert until(lambda: mail.get(f"/v1/jobs/{job['id']}").json()["state"] == "error")
    kept = mail.get("/v1/mail/accounts").json()[0]
    assert kept["state"] == "error" and "connection lost" in kept["error"]
    assert PASSWORD not in json.dumps(kept)


def test_a_mail_workspace_follows_its_mailbox_alone(mail: TestClient, tmp_path: Path) -> None:
    """Nothing is added or taken out by hand: the mailbox says what is in the graph."""
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    job = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()
    assert until(lambda: mail.get(f"/v1/jobs/{job['id']}").json()["state"] == "done")
    folder = tmp_path / "docs"
    folder.mkdir()

    answers = [
        mail.post("/v1/jobs/build", json={"folder": str(folder)}),
        mail.post("/v1/files", files={"file": ("a.pdf", b"x")}),
        mail.post("/v1/cloud/box/build"),
        mail.get("/v1/drives/onedrive/folders"),
        mail.delete("/v1/documents/doc-invented"),
        mail.post("/v1/graph/reset", json={"confirm": True}),
    ]

    assert [a.status_code for a in answers] == [409] * 6
    assert all("its own mailbox only" in a.json()["detail"] for a in answers)


def test_disconnecting_forgets_the_password_and_offers_the_workspace(
    mail: TestClient, home: Path
) -> None:
    first = mail.post("/v1/mail/accounts", json=CONNECT).json()
    second = mail.post("/v1/mail/accounts", json={**CONNECT, "address": "park@invented.test"}
                       ).json()
    mail.post(f"/v1/mail/accounts/{first['id']}/read", json={"since": None})  # opens it
    assert until(lambda: mail.get("/v1/workspaces").json()["active"] == first["workspace"])
    assert until(lambda: mail.get("/v1/jobs").json()[0]["state"] == "done")

    open_one = mail.delete(f"/v1/mail/accounts/{first['id']}?workspace=delete")
    deleted = mail.delete(f"/v1/mail/accounts/{second['id']}?workspace=delete")
    kept = mail.delete(f"/v1/mail/accounts/{first['id']}?workspace=keep")

    assert open_one.status_code == 409  # open: nothing forgotten
    assert deleted.status_code == 200
    assert kept.json() == []
    assert not (home / ".graphme" / "mail" / first["id"]).exists()
    assert not (home / ".graphme" / "mail" / second["id"]).exists()
    items = {i["id"]: i for i in mail.get("/v1/workspaces").json()["items"]}
    assert second["workspace"] not in items
    assert items[first["workspace"]]["kind"] == "documents"


def test_a_workspace_kept_on_disconnecting_keeps_its_messages_to_open(
    mail: TestClient, home: Path
) -> None:
    # Its graph names each message by its file: the files go with the account only when the
    # workspace goes too, or a kept message's original no longer opens.
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None})
    assert until(lambda: mail.get("/v1/jobs").json()[0]["state"] == "done")
    mail.post("/v1/workspaces/default/open", headers={"origin": BASE})
    messages = home / "workspaces" / account["workspace"] / "mail" / "messages"
    held = sorted(p.name for p in messages.glob("*.eml"))

    mail.delete(f"/v1/mail/accounts/{account['id']}?workspace=keep")

    assert len(held) == 2 and sorted(p.name for p in messages.glob("*.eml")) == held
    assert not (home / ".graphme" / "mail" / account["id"]).exists()  # the password is gone


def test_workspaces_written_before_kind_read_as_documents(home: Path) -> None:
    home.mkdir()
    (home / "workspaces.json").write_text(json.dumps({
        "active": "w1", "items": [{"id": "w1", "name": "Old", "mcp": True}]}), encoding="utf-8")

    item = Workspaces(home).item("w1")

    assert item["kind"] == "documents" and item["account"] is None


def test_another_sites_page_cannot_connect(mail: TestClient) -> None:
    refused = mail.post("/v1/mail/accounts", json=CONNECT,
                        headers={"origin": "https://elsewhere.invented"})

    assert refused.status_code == 403


def _running(demo: Demo) -> None:
    """A build under way, as the jobs record it."""
    demo.build = CorpusBuild(id="running", state="reading")


def test_deleting_a_mail_workspace_the_generic_way_forgets_its_account(
    mail: TestClient, home: Path
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()

    deleted = mail.delete(f"/v1/workspaces/{account['workspace']}")

    assert deleted.status_code == 200
    assert mail.get("/v1/mail/accounts").json() == []
    assert not (home / ".graphme" / "mail" / account["id"]).exists()


def test_disconnecting_an_account_whose_workspace_is_already_gone_still_forgets_it(
    mail: TestClient, workspace_demo: Demo, home: Path
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    workspace_demo.workspace_registry().delete(account["workspace"])  # not through the app

    gone = mail.delete(f"/v1/mail/accounts/{account['id']}?workspace=delete")

    assert gone.status_code == 200 and gone.json() == []
    assert not (home / ".graphme" / "mail" / account["id"]).exists()


def test_a_read_refused_because_a_build_runs_leaves_the_account_as_it_was(
    mail: TestClient, workspace_demo: Demo
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    mail.post(f"/v1/workspaces/{account['workspace']}/open")
    _running(workspace_demo)

    refused = mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": "2026-01-01"})

    assert refused.status_code == 409
    assert mail.get("/v1/mail/accounts").json() == [account]


def test_a_failure_of_any_kind_is_kept_in_the_account(
    mail: TestClient, workspace_demo: Demo, fake: FakeImap
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    mail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None})
    assert until(lambda: mail.get("/v1/mail/accounts").json()[0]["state"] == "current")
    fake.add("INBOX", _raw("later"), day=date(2026, 4, 1))
    fake.fail_fetch_error = RuntimeError("no space left on device")

    with pytest.raises(RuntimeError):
        workspace_demo.mail_accounts().sync(account["id"])

    kept = mail.get("/v1/mail/accounts").json()[0]
    assert kept["state"] == "error" and "no space left" in kept["error"]


def test_an_account_is_not_disconnected_while_its_workspace_builds(
    mail: TestClient, workspace_demo: Demo
) -> None:
    account = mail.post("/v1/mail/accounts", json=CONNECT).json()
    mail.post(f"/v1/workspaces/{account['workspace']}/open")
    _running(workspace_demo)

    refused = mail.delete(f"/v1/mail/accounts/{account['id']}?workspace=keep")

    assert refused.status_code == 409
    assert [a["id"] for a in mail.get("/v1/mail/accounts").json()] == [account["id"]]
