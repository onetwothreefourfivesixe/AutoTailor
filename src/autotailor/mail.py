"""Mail intake (M7): every way a Runway email reaches AutoTailor ends in `import_raw`.

- Drop folder (default): save the email as a .eml file into data/inbox/.
- IMAP (optional): pull new mail from one Gmail label, read-only.
- SMTP listener (optional): a mail server on 127.0.0.1 that a desktop mail
  client can forward to.

Only mail from `mail.allowed_senders` (Runway) is read; anything else is
ignored. Each message is imported once (by Message-ID). A role becomes a
placeholder posting with no description, or folds into a posting already
stored from an employer's job board; resolve.py finds the rest.
"""

from __future__ import annotations

import imaplib
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from autotailor import db
from autotailor.config import MailSettings, Settings
from autotailor.ingest import title_matches
from autotailor.sources import runway

IMAP_PASSWORD_ENV = "AUTOTAILOR_IMAP_PASSWORD"
KEYRING_SERVICE = "autotailor-imap"


@dataclass
class MailResult:
    messages: int = 0  # Runway emails found
    already: int = 0  # imported before
    ignored: int = 0  # not from Runway
    roles: int = 0
    new: list[str] = field(default_factory=list)  # posting IDs created
    merged: int = 0  # roles already stored (from a job board or an earlier email)

    def add(self, other: MailResult) -> None:
        for f in ("messages", "already", "ignored", "roles", "merged"):
            setattr(self, f, getattr(self, f) + getattr(other, f))
        self.new += other.new

    def summary(self) -> str:
        parts = [f"{self.messages} Runway emails ({self.already} seen before)", f"{self.roles} roles",
                 f"{len(self.new)} new", f"{self.merged} already stored"]  # fmt: skip
        if self.ignored:
            parts.append(f"{self.ignored} other emails ignored")
        return ", ".join(parts)


def import_raw(conn: sqlite3.Connection, raw: bytes, origin: str, settings: Settings) -> MailResult:
    """Read one email (raw RFC 822 bytes) and store the Runway roles in it."""
    out = MailResult()
    msg = runway.parse_message(raw)
    found = runway.runway_messages(msg, settings.mail.allowed_senders)
    if not found:
        out.ignored = 1
        return out
    for m in found:
        digest = runway.parse_digest(m)
        out.messages += 1
        if db.mail_seen(conn, digest.message_id):
            out.already += 1
            continue
        for role in digest.roles:
            out.roles += 1
            if db.runway_role(conn, role.job_id):  # same role in an earlier email
                out.merged += 1
                continue
            # Runway's radar is already the user's own filter, so only title_exclude applies.
            if not title_matches(role.title, [], settings.sources.title_exclude):
                db.add_runway_role(conn, role.job_id, None, role.company, role.title, role.location,
                                   role.company_domain, role.match_score, digest.message_id, "skipped")  # fmt: skip
                continue
            p = role.to_posting()
            existing = db.find_duplicate(conn, p)
            if existing:
                db.fill_missing(conn, existing, p)  # adds Runway's match score if missing
                out.merged += 1
                stored = db.get_posting(conn, existing)
                status = "resolved" if stored and stored.description else "open"
                db.add_runway_role(conn, role.job_id, existing, role.company, role.title, role.location,
                                   role.company_domain, role.match_score, digest.message_id, status)  # fmt: skip
            else:
                db.upsert_posting(conn, p)
                out.new.append(p.id)
                db.add_runway_role(conn, role.job_id, p.id, role.company, role.title, role.location,
                                   role.company_domain, role.match_score, digest.message_id)  # fmt: skip
        db.record_mail(conn, digest.message_id, origin, runway.sender(m) or None, digest.subject,
                       digest.sent_at, len(digest.roles))  # fmt: skip
    return out


# --- D: drop folder ----------------------------------------------------------------------


def import_folder(conn: sqlite3.Connection, inbox: Path, settings: Settings) -> MailResult:
    """Import every .eml file in `inbox`, then move it to inbox/done/ (or inbox/ignored/)."""
    total = MailResult()
    for path in sorted(inbox.glob("*.eml")):
        result = import_file(conn, path, settings)
        total.add(result)
        dest = inbox / ("ignored" if result.ignored and not result.messages else "done")
        dest.mkdir(exist_ok=True)
        shutil.move(str(path), str(_free_name(dest / path.name)))
    return total


def import_file(conn: sqlite3.Connection, path: Path, settings: Settings, origin: str = "drop_folder") -> MailResult:
    return import_raw(conn, path.read_bytes(), origin, settings)


def _free_name(path: Path) -> Path:
    n, candidate = 2, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate


def save_to_inbox(inbox: Path, raw: bytes, origin: str) -> Path:
    """Keep a copy of a received email in the drop folder (as if saved by hand)."""
    inbox.mkdir(parents=True, exist_ok=True)
    path = _free_name(inbox / f"{datetime.now():%Y%m%d-%H%M%S}-{origin}.eml")
    path.write_bytes(raw)
    return path


# --- B: IMAP label pull ---------------------------------------------------------------------


def imap_password(env_file: Path | None = None) -> str | None:
    """The IMAP app password: the OS keyring if it has one, else AUTOTAILOR_IMAP_PASSWORD
    (environment or .env). Never config.toml."""
    try:
        import keyring

        pw = keyring.get_password(KEYRING_SERVICE, "imap")
        if pw:
            return pw
    except Exception:  # no usable keyring (e.g. WSL, headless Linux)
        pass
    if os.environ.get(IMAP_PASSWORD_ENV):
        return os.environ[IMAP_PASSWORD_ENV]
    if env_file and env_file.exists():
        from dotenv import dotenv_values

        return dotenv_values(env_file).get(IMAP_PASSWORD_ENV) or None
    return None


def store_password_in_keyring(password: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, "imap", password)


def _imap_date(d: datetime) -> str:
    return d.strftime("%d-%b-%Y")


def fetch_imap(mail: MailSettings, password: str, days: int, connect=imaplib.IMAP4_SSL) -> list[bytes]:  # noqa: ANN001
    """Raw messages from the allowed senders in `mail.imap_label`, from the last `days` days.

    The label is opened read-only (EXAMINE) and bodies are fetched with
    BODY.PEEK, so nothing is marked as read or changed in the mailbox.
    """
    imap = connect(mail.imap_host)
    try:
        imap.login(mail.imap_user, password)
        typ, _ = imap.select(f'"{mail.imap_label}"', readonly=True)
        if typ != "OK":
            raise RuntimeError(f"No mailbox or label named '{mail.imap_label}'.")
        since = _imap_date(datetime.now() - timedelta(days=days))
        uids: list[bytes] = []
        for s in mail.allowed_senders:
            typ, data = imap.uid("SEARCH", None, "SINCE", since, "FROM", f'"{s}"')
            if typ == "OK" and data and data[0]:
                uids += data[0].split()
        out = []
        for uid in dict.fromkeys(uids):
            typ, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ == "OK":
                out += [part[1] for part in data if isinstance(part, tuple) and len(part) > 1]
        return out
    finally:
        try:
            imap.logout()
        except Exception:
            pass


# --- A: local SMTP listener ---------------------------------------------------------------------


class _Handler:
    """aiosmtpd handler: accepts Runway mail (sent or forwarded) and saves it to the inbox."""

    def __init__(self, inbox: Path, allowed: list[str], on_saved=None) -> None:  # noqa: ANN001
        self.inbox, self.allowed, self.on_saved = inbox, allowed, on_saved

    async def handle_DATA(self, server, session, envelope):  # noqa: ANN001, N802
        raw = envelope.original_content or envelope.content
        if isinstance(raw, str):
            raw = raw.encode()
        if not runway.runway_messages(runway.parse_message(raw), self.allowed):
            return "550 Not a Runway email; AutoTailor only accepts those"
        path = save_to_inbox(self.inbox, raw, "smtp")
        if self.on_saved:
            self.on_saved(path)
        return "250 Saved"


def smtp_controller(inbox: Path, allowed: list[str], port: int, on_saved=None):  # noqa: ANN001, ANN201
    """A local SMTP server on 127.0.0.1:`port`. Call .start() / .stop()."""
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import SMTP

    class _LenientSMTP(SMTP):
        # Mail clients forwarding HTML email sometimes send lines over SMTP's 1000-character
        # limit. This server only listens on 127.0.0.1, so accepting them is harmless.
        line_length_limit = 1_000_000

    class _Controller(Controller):
        def factory(self):  # noqa: ANN202
            return _LenientSMTP(self.handler, **self.SMTP_kwargs)

    return _Controller(_Handler(inbox, allowed, on_saved), hostname="127.0.0.1", port=port)


def mask(addr: str) -> str:
    """'edward@gmail.com' -> 'e***@gmail.com', for printing."""
    return re.sub(r"^(.).*(@.*)$", r"\1***\2", addr)
