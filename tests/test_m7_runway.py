"""M7 plumbing: Runway email parsing, the three mail intakes, and the resolver.

No network: job boards, IMAP and the extraction chain are faked. The email
fixture is synthetic (same structure as a real Runway digest).
"""

from __future__ import annotations

import smtplib
import socket
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autotailor import db, mail
from autotailor.chains.extract_posting import PostingFields
from autotailor.cli import app
from autotailor.config import Settings
from autotailor.models import JobPosting, PostingStatus, Source
from autotailor.resolve import Resolver, best_match, slug_guesses
from autotailor.sources import runway

FIXTURE = Path(__file__).parent / "fixtures" / "runway_digest.eml"
RAW = FIXTURE.read_bytes()
SENDERS = ["hello@tower.joinrunway.io"]


# --- parsing --------------------------------------------------------------------------


def test_parse_digest():
    msgs = runway.runway_messages(runway.parse_message(RAW), SENDERS)
    d = runway.parse_digest(msgs[0])
    assert d.message_id == "<fixture-0001@mtasv.net>"
    assert [(r.job_id, r.company, r.title, r.location, r.match_score, r.company_domain) for r in d.roles] == [
        ("rwjob0001aaaa", "Acme Robotics", "Software Engineering Intern (Summer 2027)", "San Jose, California", 0.95, "acmerobotics.com"),
        ("rwjob0002bbbb", "Northwind", "ML Research Intern", "Mountain View, California", 0.88, "northwind.ai"),
        ("rwjob0003cccc", "Globex", "Senior Staff Engineer", "New York, New York", 0.70, "globex.com"),
    ]  # fmt: skip
    assert d.roles[0].posted_at == datetime(2026, 11, 12, 22, 0, tzinfo=UTC)  # 16 hours before the Date header
    assert d.roles[0].runway_url == "https://app.joinrunway.io/job/rwjob0001aaaa"  # tracking wrapper removed


@pytest.mark.parametrize(
    ("text", "hours"),
    [("Posted about 16 hours ago", 16), ("Posted 1 hour ago", 1), ("Posted about a day ago", 24),
     ("Posted 3 days ago", 72), ("Posted less than a minute ago", 0)],
)  # fmt: skip
def test_posted_at(text, hours):
    sent = datetime(2026, 11, 13, 12, tzinfo=UTC)
    assert (sent - runway.posted_at(text, sent)).total_seconds() == hours * 3600


def _forward_as_attachment() -> bytes:
    outer = EmailMessage()
    outer["From"], outer["To"], outer["Subject"] = "sam@example.com", "inbox@autotailor.local", "Fwd: roles"
    outer.set_content("see attached")
    outer.add_attachment(runway.parse_message(RAW), filename="runway.eml")
    return bytes(outer)


def _forward_inline() -> bytes:
    html = runway.parse_message(RAW).get_body(("html",)).get_content()
    m = EmailMessage()
    m["From"], m["Subject"] = "sam@example.com", "Fwd: These 3 roles went live today"
    m["Message-ID"] = "<inline-fwd@example.com>"
    m.set_content("<div>---------- Forwarded message ---------<br>From: Ford from Runway &lt;hello@tower.joinrunway.io&gt;"
                  "<br>Date: Fri, Nov 13, 2026</div>" + html, subtype="html")  # fmt: skip
    return bytes(m)


def test_finds_forwarded_runway_mail_and_ignores_other_mail():
    as_attachment = runway.runway_messages(runway.parse_message(_forward_as_attachment()), SENDERS)
    assert len(as_attachment) == 1 and len(runway.parse_digest(as_attachment[0]).roles) == 3
    inline = runway.runway_messages(runway.parse_message(_forward_inline()), SENDERS)
    assert len(inline) == 1 and len(runway.parse_digest(inline[0]).roles) == 3
    other = EmailMessage()
    other["From"] = "news@example.com"
    other.set_content("<a href='https://app.joinrunway.io/job/x'>x</a>", subtype="html")
    assert runway.runway_messages(other, SENDERS) == []


# --- import -----------------------------------------------------------------------------


def _settings() -> Settings:
    s = Settings()
    s.sources.title_exclude = ["senior"]
    return s


def _board_posting(pid="greenhouse:acmerobotics:1", title="Software Engineering Intern (Summer 2027)",
                   location="San Jose, CA", description="Full posting text") -> JobPosting:  # fmt: skip
    src, slug, ref = pid.split(":")
    return JobPosting(id=pid, source=Source(src), source_ref=ref, company="Acme Robotics", title=title,
                      location=location, description=description, canonical_url=f"https://boards.greenhouse.io/{slug}/jobs/{ref}",
                      status=PostingStatus.RESOLVED)  # fmt: skip


def test_import_creates_placeholders_dedupes_and_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_posting(conn, _board_posting(location="San Jose, California"))  # already fetched from the board
    r = mail.import_raw(conn, RAW, "drop_folder", _settings())
    assert (r.messages, r.roles, r.new, r.merged) == (1, 3, ["runway:rwjob0002bbbb"], 1)
    board = db.get_posting(conn, "greenhouse:acmerobotics:1")
    assert board.external_score == 0.95  # Runway's score folded into the board posting
    placeholder = db.get_posting(conn, "runway:rwjob0002bbbb")
    assert placeholder.status == PostingStatus.UNRESOLVED and placeholder.description is None
    statuses = {r["job_id"]: r["status"] for r in db.list_runway_roles(conn)}
    assert statuses == {"rwjob0001aaaa": "resolved", "rwjob0002bbbb": "open", "rwjob0003cccc": "skipped"}  # 'senior' excluded

    again = mail.import_raw(conn, RAW, "imap", _settings())
    assert (again.messages, again.already, again.new) == (1, 1, [])


def test_import_folder_moves_files(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.eml").write_bytes(RAW)
    junk = EmailMessage()
    junk["From"] = "news@example.com"
    junk.set_content("hello")
    (inbox / "b.eml").write_bytes(bytes(junk))
    conn = db.connect(tmp_path / "a.db")
    r = mail.import_folder(conn, inbox, _settings())
    assert r.messages == 1 and r.ignored == 1
    assert (inbox / "done" / "a.eml").exists() and (inbox / "ignored" / "b.eml").exists()
    assert not list(inbox.glob("*.eml"))


class FakeIMAP:
    def __init__(self, host):
        self.host, self.calls = host, []

    def login(self, user, pw):
        self.calls.append(("login", user))

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        self.calls.append(("uid", cmd, args))
        if cmd == "SEARCH":
            return ("OK", [b"7 9"])
        return ("OK", [(b"7 (BODY[] {123}", RAW), b")"])

    def logout(self):
        pass


def test_imap_pull_is_read_only(monkeypatch):
    s = _settings()
    s.mail.imap_user, s.mail.imap_label = "sam@example.com", "Runway"
    made: list[FakeIMAP] = []

    def connect(host):
        made.append(FakeIMAP(host))
        return made[-1]

    raws = mail.fetch_imap(s.mail, "app-password", 14, connect=connect)
    assert raws == [RAW, RAW]
    calls = made[0].calls
    assert ("select", '"Runway"', True) in calls  # EXAMINE: read-only
    assert all("PEEK" in str(c) for c in calls if c[:2] == ("uid", "FETCH"))  # never marks mail read
    assert any("hello@tower.joinrunway.io" in str(c) for c in calls if c[:2] == ("uid", "SEARCH"))


def test_imap_password_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv(mail.IMAP_PASSWORD_ENV, raising=False)
    monkeypatch.setattr("keyring.get_password", lambda *a: None)
    env = tmp_path / ".env"
    env.write_text(f"{mail.IMAP_PASSWORD_ENV}=abcd efgh\n")
    assert mail.imap_password(env) == "abcd efgh"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_smtp_listener_accepts_only_runway(tmp_path):
    saved: list[Path] = []
    port = _free_port()
    ctl = mail.smtp_controller(tmp_path / "inbox", SENDERS, port, saved.append)
    ctl.start()
    try:
        with smtplib.SMTP("127.0.0.1", port) as smtp:
            smtp.sendmail("sam@example.com", ["inbox@autotailor.local"], _forward_as_attachment())
            with pytest.raises(smtplib.SMTPDataError):
                smtp.sendmail("news@example.com", ["inbox@autotailor.local"], b"From: news@example.com\r\n\r\nhello")
    finally:
        ctl.stop()
    assert len(saved) == 1 and saved[0].parent == tmp_path / "inbox"


# --- resolving ------------------------------------------------------------------------------


def test_slug_guesses_and_best_match():
    assert slug_guesses("Muon Space", "muonspace.com") == ["muonspace", "muon-space", "muon"]
    assert slug_guesses("Nuro, Inc.", "nuro.ai") == ["nuro"]
    role = runway.RunwayRole("x", "Software Engineer - Internship", "Octant", "Emeryville, California", None, None, None).to_posting()
    board = [_board_posting("greenhouse:octant:1", "Software Engineer, Internship", "Emeryville, CA"),
             _board_posting("greenhouse:octant:2", "Software Engineer - Internship", "Boston, MA")]  # fmt: skip
    assert best_match(role, board).id == "greenhouse:octant:1"  # same city wins; punctuation ignored
    assert best_match(role, [_board_posting("greenhouse:octant:3", "Hardware Engineer Intern")]) is None


class FakeBoards:
    def __init__(self, boards: dict[tuple[str, str], list[JobPosting]]):
        self.boards, self.calls = boards, []

    def __call__(self, client, source, slug):
        self.calls.append((source.value, slug))
        if (source.value, slug) not in self.boards:
            raise __import__("httpx").HTTPStatusError("404", request=None, response=type("R", (), {"status_code": 404})())
        return self.boards[(source.value, slug)]


def _imported(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    mail.import_raw(conn, RAW, "drop_folder", _settings())
    return conn


def test_auto_match_finds_the_board_and_learns_it(tmp_path):
    conn = _imported(tmp_path)
    nw = JobPosting(id="ashby:northwind:9", source=Source.ASHBY, source_ref="9", company="Northwind", title="ML Research Intern",
                    location="Mountain View, CA", description="text", canonical_url="https://jobs.ashbyhq.com/northwind/9",
                    status=PostingStatus.RESOLVED)  # fmt: skip
    fetch = FakeBoards({("ashby", "northwind"): [nw]})
    r = Resolver(conn, None, _settings(), fetch=fetch)
    assert r.auto_match("runway:rwjob0001aaaa") is False  # Acme isn't on any (fake) board
    assert db.get_posting(conn, "runway:rwjob0001aaaa").status == PostingStatus.NEEDS_LINK

    assert r.auto_match("runway:rwjob0002bbbb") is True
    assert db.get_posting(conn, "runway:rwjob0002bbbb") is None  # placeholder replaced
    stored = db.get_posting(conn, "ashby:northwind:9")
    assert stored.external_score == 0.88 and stored.description == "text"
    assert db.runway_role(conn, "rwjob0002bbbb")["posting_id"] == "ashby:northwind:9"
    assert db.learned_board(conn, "northwind") == ("ashby", "northwind")
    assert ("greenhouse", "northwind") in fetch.calls  # guessed from the domain northwind.ai


def test_apply_answer(tmp_path):
    conn = _imported(tmp_path)
    extract = lambda text: PostingFields(company="Acme Robotics", title="Software Engineering Intern (Summer 2027)", location="San Jose, CA")  # noqa: E731
    r = Resolver(conn, None, _settings(), extract=extract, fetch=FakeBoards({}))
    pid = "runway:rwjob0001aaaa"
    err = r.apply_answer(pid, {"url": "https://app.joinrunway.io/job/rwjob0001aaaa"})
    assert "joinrunway.io" in err and db.get_posting(conn, pid) is not None  # refused, nothing fetched
    assert "Give a URL" in r.apply_answer(pid, {"url": "  "})

    assert r.apply_answer(pid, {"text": "About the role. " * 30}) is None
    role = db.runway_role(conn, "rwjob0001aaaa")
    new = db.get_posting(conn, role["posting_id"])
    assert role["status"] == "resolved" and new.source == Source.PASTE and new.external_score == 0.95
    assert db.get_posting(conn, pid) is None

    assert r.apply_answer("runway:rwjob0002bbbb", {"skip": True}) is None
    assert db.runway_role(conn, "rwjob0002bbbb")["status"] == "skipped"
    assert db.get_posting(conn, "runway:rwjob0002bbbb") is None


# --- CLI ------------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    (tmp_path / "data" / "inbox").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "inbox" / "digest.eml").write_bytes(RAW)
    monkeypatch.setattr("autotailor.resolve.fetch_board", FakeBoards({}))
    return tmp_path


def test_cli_mail_import_and_runway_list(home):
    runner = CliRunner()
    r = runner.invoke(app, ["mail", "import"])
    assert r.exit_code == 0, r.output
    assert "1 Runway emails" in r.output and "3 new" in r.output and "runway resolve" in r.output
    r = runner.invoke(app, ["runway", "list"])
    assert "Acme Robotics | Software Engineering Intern" in r.output and "95%" in r.output


def test_cli_resolve_auto_leaves_unmatched_roles_waiting(home):
    runner = CliRunner()
    runner.invoke(app, ["mail", "import"])
    r = runner.invoke(app, ["runway", "resolve", "--auto"])
    assert r.exit_code == 0, r.output
    assert "0 resolved, 0 skipped, 3 waiting" in r.output
    conn = db.connect(home / "data" / "autotailor.db")
    assert db.get_posting(conn, "runway:rwjob0001aaaa").status == PostingStatus.NEEDS_LINK
    conn.close()


def test_runway_page_renders(home):
    from streamlit.testing.v1 import AppTest

    CliRunner().invoke(app, ["mail", "import"])
    at = AppTest.from_string("from autotailor.ui import page_runway\npage_runway.render()", default_timeout=30).run()
    assert not at.exception, at.exception
    assert at.dataframe and len(at.dataframe[0].value) == 3
