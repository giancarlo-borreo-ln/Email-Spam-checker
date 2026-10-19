#!/usr/bin/env python3
"""
cleanup_mail.py - tidy up a Gmail / IMAP mailbox.

What it does
------------
1. Moves messages that carry provider spam headers (X-Spam-Flag: YES or
   X-Spam-Status: Yes), or that match --spam-from / --spam-subject /
   --spam-contains rules, into the mailbox's Spam/Junk folder and marks
   them read.
2. Marks unread messages as read.

Sign-in
-------
Run it with no arguments and it asks for your email address and either:
  * an app password (simplest), or
  * Google OAuth, which opens your browser (requires credentials.json).

Safety model
------------
* Changes are applied after a short summary and a [Y/n] confirmation.
* --dry-run previews everything without changing anything.
* --yes skips the confirmation.
* Every change is written to a CSV audit log (folder + UID), so you can see
  exactly what happened.
* --limit N caps how many messages are touched per folder per action.

No third-party dependencies unless you use --auth oauth.
"""

from __future__ import annotations

import argparse
import base64
import csv
import getpass
import imaplib
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path

DEFAULT_HOST = "imap.gmail.com"
DEFAULT_PORT = 993
DEFAULT_FOLDER = "INBOX"

COMMAND_CHUNK = 200  # UIDs per STORE/MOVE/COPY command
FETCH_CHUNK = 100    # UIDs per FETCH command
PAUSE = 0.15         # seconds between commands, to be gentle with the server

# Gmail uses \Spam, RFC 6154 defines \Junk. Accept both.
SPAM_FLAGS = {"\\JUNK", "\\SPAM"}
# Folders we never treat as cleanup targets.
EXCLUDED_FLAGS = {
    "\\NOSELECT", "\\TRASH", "\\DRAFTS", "\\SENT", "\\ALL",
    "\\JUNK", "\\SPAM", "\\ARCHIVE",
}
SPAM_NAME_HINTS = {
    "spam", "junk", "junk email", "junk e-mail", "bulk mail",
    "posta indesiderata", "indesiderata", "courrier indesirable",
    "correo no deseado", "spamordner",
}

ENV_KEYS_USER = ("EMAIL_ADDRESS", "GMAIL_ADDRESS", "IMAP_USER")
ENV_KEYS_PASSWORD = ("EMAIL_APP_PASSWORD", "EMAIL_PASSWORD", "GMAIL_APP_PASSWORD", "IMAP_PASSWORD")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def log(message: str = "", *, file=None) -> None:
    print(message, file=file or sys.stdout, flush=True)


def chunked(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def uid_set(uids: list[bytes]) -> bytes:
    return b",".join(uids)


def quote_mailbox(name: str) -> str:
    """Quote a mailbox name for an IMAP command."""
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def quote_search(value: str) -> str:
    """Quote a string for an IMAP SEARCH argument."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def decode_imap_utf7(value: str) -> str:
    """Decode IMAP's modified UTF-7 folder names (for display only)."""
    def replace(match: re.Match[str]) -> str:
        chunk = match.group(1)
        if chunk == "":
            return "&"
        padded = chunk.replace(",", "/")
        padded += "=" * (-len(padded) % 4)
        try:
            return base64.b64decode(padded).decode("utf-16-be")
        except Exception:
            return "&" + chunk + "-"

    return re.sub(r"&([A-Za-z0-9+,]*)-", replace, value)


def decode_header_value(value) -> str:
    if value is None:
        return ""
    try:
        return " ".join(str(value).split())
    except Exception:
        return repr(value)


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def resolve_env_file(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


@dataclass
class Auth:
    user: str
    mode: str                        # "app-password" or "oauth"
    password: str | None = None
    credentials_path: str = "credentials.json"
    token_path: str = "token.json"
    reauth: bool = False


def _first_value(file_values: dict[str, str], keys, from_args=None):
    if from_args:
        return from_args
    for key in keys:
        if file_values.get(key):
            return file_values[key]
        if os.environ.get(key):
            return os.environ[key]
    return None


def choose_auth_mode(args) -> str:
    """Decide how to sign in when --auth auto is used."""
    if args.auth in ("app-password", "oauth"):
        return args.auth
    if Path(args.token).expanduser().exists() or Path(args.credentials).expanduser().exists():
        return "oauth"
    if not sys.stdin.isatty():
        return "app-password"

    print("How do you want to sign in?")
    print("  1) App password (simplest; 16 characters from your Google account)")
    print("  2) Google OAuth (opens your browser; needs a one-time credentials.json)")
    choice = input("Choose [1]: ").strip().lower()
    return "oauth" if choice in ("2", "oauth") else "app-password"


def resolve_auth(args) -> Auth:
    env_path = resolve_env_file(args.env_file)
    file_values = load_env_file(env_path) if env_path else {}

    user = _first_value(file_values, ENV_KEYS_USER, args.user)
    if not user:
        user = input("Email address: ").strip()
    if not user:
        raise SystemExit("Missing email address.")

    mode = choose_auth_mode(args)
    auth = Auth(
        user=user,
        mode=mode,
        credentials_path=args.credentials,
        token_path=args.token,
        reauth=args.reauth,
    )

    if mode == "oauth":
        credentials_file = Path(args.credentials).expanduser()
        token_file = Path(args.token).expanduser()
        if not credentials_file.exists() and not token_file.exists():
            raise SystemExit(
                "OAuth needs a Google OAuth client file.\n\n"
                "One-time setup:\n"
                "  1. Go to https://console.cloud.google.com/ and create a project.\n"
                "  2. APIs & Services > Library > enable 'Gmail API'.\n"
                "  3. OAuth consent screen: choose External, add yourself as a test user.\n"
                "  4. Credentials > Create credentials > OAuth client ID > Desktop app.\n"
                "  5. Download the JSON, save it next to this script as credentials.json.\n\n"
                "Then run the script again; your browser will open for sign-in."
            )
        return auth

    password = _first_value(file_values, ENV_KEYS_PASSWORD, args.password)
    if not password:
        password = getpass.getpass(f"App password for {user}: ")
    if not password:
        raise SystemExit("Missing app password.")
    auth.password = password
    return auth


# --------------------------------------------------------------------------- #
# IMAP plumbing
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Folder:
    raw: str          # exactly as returned by LIST; use this in commands
    name: str         # decoded, for display
    flags: frozenset[str]

    def selectable(self) -> bool:
        return "\\NOSELECT" not in self.flags


def parse_folder_entry(entry) -> Folder | None:
    if isinstance(entry, tuple):
        entry = b" ".join(part for part in entry if isinstance(part, bytes))
    if not isinstance(entry, bytes):
        return None
    text = entry.decode("utf-8", "replace")
    match = re.match(
        r'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|"(?:[^"\\]|\\.)*")\s+(?P<name>.+)$',
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    flags = frozenset(flag.upper() for flag in match.group("flags").split())
    name = match.group("name").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return Folder(raw=name, name=decode_imap_utf7(name), flags=flags)


def list_folders(conn: imaplib.IMAP4) -> list[Folder]:
    status, data = conn.list()
    if status != "OK":
        raise RuntimeError(f"LIST failed: {status}")
    folders = []
    for entry in data:
        folder = parse_folder_entry(entry)
        if folder:
            folders.append(folder)
    return folders


def select_folder(conn: imaplib.IMAP4, folder: Folder | str, readonly: bool = False) -> int:
    raw = folder.raw if isinstance(folder, Folder) else folder
    status, data = conn.select(quote_mailbox(raw), readonly=readonly)
    if status != "OK":
        raise RuntimeError(f"Could not select {raw!r}: {data!r}")
    try:
        return int(data[0])
    except (TypeError, ValueError, IndexError):
        return 0


def server_capabilities(conn: imaplib.IMAP4) -> set[str]:
    try:
        status, data = conn.capability()
        if status == "OK" and data and data[0]:
            blob = data[0]
            if isinstance(blob, bytes):
                blob = blob.decode("ascii", "replace")
            return {part.upper() for part in blob.split()}
    except imaplib.IMAP4.error:
        pass
    return {cap.upper() for cap in conn.capabilities}


def uid_search(conn: imaplib.IMAP4, *criteria: str) -> list[bytes]:
    status, data = conn.uid("SEARCH", None, *criteria)
    if status != "OK":
        raise RuntimeError(f"SEARCH {criteria!r} failed: {data!r}")
    if not data or not data[0]:
        return []
    return data[0].split()


def fetch_headers(conn: imaplib.IMAP4, uids: list[bytes], fields: list[str]) -> dict[bytes, bytes]:
    """Fetch selected header fields. Returns {uid: raw_header_bytes}."""
    result: dict[bytes, bytes] = {}
    field_list = "(" + " ".join(fields) + ")"
    for chunk in chunked(uids, FETCH_CHUNK):
        status, data = conn.uid(
            "FETCH",
            uid_set(chunk),
            f"(UID BODY.PEEK[HEADER.FIELDS {field_list}])",
        )
        if status != "OK":
            continue
        for item in data or []:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            meta, payload = item
            match = re.search(rb"UID (\d+)", meta)
            if match and isinstance(payload, bytes):
                result[match.group(1)] = payload
        time.sleep(PAUSE)
    return result


def mark_seen(conn: imaplib.IMAP4, uids: list[bytes]) -> int:
    changed = 0
    for chunk in chunked(uids, COMMAND_CHUNK):
        status, _ = conn.uid("STORE", uid_set(chunk), "+FLAGS.SILENT", "(\\Seen)")
        if status == "OK":
            changed += len(chunk)
        time.sleep(PAUSE)
    return changed


def mark_unseen(conn: imaplib.IMAP4, uids: list[bytes]) -> int:
    changed = 0
    for chunk in chunked(uids, COMMAND_CHUNK):
        status, _ = conn.uid("STORE", uid_set(chunk), "-FLAGS.SILENT", "(\\Seen)")
        if status == "OK":
            changed += len(chunk)
        time.sleep(PAUSE)
    return changed


def move_messages(
    conn: imaplib.IMAP4,
    uids: list[bytes],
    destination: str,
    capabilities: set[str],
) -> int:
    """Move messages to `destination`. Uses MOVE when available."""
    moved = 0
    can_move = "MOVE" in capabilities
    can_uid_expunge = "UIDPLUS" in capabilities
    warned_expunge = False

    for chunk in chunked(uids, COMMAND_CHUNK):
        chunk_uids = uid_set(chunk)
        if can_move:
            status, _ = conn.uid("MOVE", chunk_uids, quote_mailbox(destination))
        else:
            status, _ = conn.uid("COPY", chunk_uids, quote_mailbox(destination))
            if status == "OK":
                conn.uid("STORE", chunk_uids, "+FLAGS.SILENT", "(\\Deleted)")
                if can_uid_expunge:
                    status, _ = conn.uid("EXPUNGE", chunk_uids)
                else:
                    if not warned_expunge:
                        log("  ! Server lacks MOVE/UIDPLUS; EXPUNGE may also remove")
                        log("    other \\Deleted messages in this folder.")
                        warned_expunge = True
                    status, _ = conn.expunge()
        if status == "OK":
            moved += len(chunk)
        time.sleep(PAUSE)
    return moved


def is_header_spam(payload: bytes) -> bool:
    message = BytesHeaderParser(policy=policy.default).parsebytes(payload)
    flag = str(message.get("X-Spam-Flag", "")).strip().upper()
    status = str(message.get("X-Spam-Status", "")).strip().upper()
    return flag.startswith("YES") or status.startswith("YES")


def find_provider_spam(conn: imaplib.IMAP4) -> list[bytes]:
    """Return UIDs whose provider-added headers explicitly mark them as spam."""
    candidates: list[bytes] = []
    seen: set[bytes] = set()
    for criteria in (
        ("HEADER", "X-Spam-Flag", "YES"),
        ("HEADER", "X-Spam-Status", "Yes"),
    ):
        for uid in uid_search(conn, *criteria):
            if uid not in seen:
                seen.add(uid)
                candidates.append(uid)

    if not candidates:
        return []

    # IMAP HEADER search is a substring match, so verify the actual values.
    headers = fetch_headers(conn, candidates, ["X-Spam-Flag", "X-Spam-Status"])
    confirmed = [uid for uid in candidates if uid in headers and is_header_spam(headers[uid])]
    return sorted(confirmed, key=int)


@dataclass(frozen=True)
class SpamRules:
    """User-supplied case-insensitive substring rules."""

    from_patterns: tuple[str, ...] = ()
    subject_patterns: tuple[str, ...] = ()
    contains_patterns: tuple[str, ...] = ()

    @classmethod
    def from_args(cls, args) -> "SpamRules":
        def clean(values):
            patterns = tuple(value.strip() for value in values or () if value.strip())
            for pattern in patterns:
                try:
                    pattern.encode("ascii")
                except UnicodeEncodeError:
                    raise SystemExit(
                        f"Spam rule must be ASCII (IMAP search limit): {pattern!r}"
                    ) from None
            return patterns

        return cls(
            from_patterns=clean(args.spam_from),
            subject_patterns=clean(args.spam_subject),
            contains_patterns=clean(args.spam_contains),
        )

    def active(self) -> bool:
        return bool(self.from_patterns or self.subject_patterns or self.contains_patterns)


def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    haystack = value.casefold()
    return any(pattern.casefold() in haystack for pattern in patterns)


def is_rule_spam(payload: bytes, rules: SpamRules) -> bool:
    message = BytesHeaderParser(policy=policy.default).parsebytes(payload)
    sender = decode_header_value(message.get("From"))
    subject = decode_header_value(message.get("Subject"))
    contains = rules.contains_patterns
    return (
        _matches_any(sender, rules.from_patterns)
        or _matches_any(subject, rules.subject_patterns)
        or _matches_any(sender, contains)
        or _matches_any(subject, contains)
    )


def find_rule_spam(conn: imaplib.IMAP4, rules: SpamRules) -> list[bytes]:
    """Return UIDs matched by the sender/subject rules.

    The patterns are searched server-side, then the fetched headers are checked
    again locally so a loose server match cannot cause a false positive.
    """
    if not rules.active():
        return []

    criteria: list[tuple[str, str]] = []
    criteria.extend(("FROM", pattern) for pattern in rules.from_patterns)
    criteria.extend(("SUBJECT", pattern) for pattern in rules.subject_patterns)
    for pattern in rules.contains_patterns:
        criteria.append(("FROM", pattern))
        criteria.append(("SUBJECT", pattern))

    candidates: set[bytes] = set()
    for field, pattern in criteria:
        candidates.update(uid_search(conn, field, quote_search(pattern)))

    if not candidates:
        return []

    uids = sorted(candidates, key=int)
    headers = fetch_headers(conn, uids, ["From", "Subject"])
    confirmed = [uid for uid in uids if uid in headers and is_rule_spam(headers[uid], rules)]
    return confirmed


# --------------------------------------------------------------------------- #
# Folder selection / reporting
# --------------------------------------------------------------------------- #

def pick_spam_folder(folders: list[Folder], override: str | None) -> Folder:
    if override:
        for folder in folders:
            if folder.raw == override or folder.name == override:
                return folder
        return Folder(raw=override, name=override, flags=frozenset())

    for folder in folders:
        if folder.flags & SPAM_FLAGS:
            return folder
    for folder in folders:
        if folder.name.strip().lower() in SPAM_NAME_HINTS:
            return folder
    raise SystemExit(
        "Could not find a Spam/Junk folder. Use --list-folders and --spam-folder NAME."
    )


def choose_target_folders(
    folders: list[Folder],
    requested: list[str] | None,
    all_folders: bool,
    spam_folder: Folder,
) -> list[Folder]:
    by_name = {folder.name: folder for folder in folders}
    by_raw = {folder.raw: folder for folder in folders}

    if all_folders:
        targets = []
        for folder in folders:
            if not folder.selectable():
                continue
            if folder.flags & EXCLUDED_FLAGS:
                continue
            if folder.raw == spam_folder.raw:
                continue
            targets.append(folder)
        return targets

    names = requested or [DEFAULT_FOLDER]
    targets = []
    for name in names:
        folder = by_raw.get(name) or by_name.get(name)
        if not folder:
            raise SystemExit(f"Folder not found: {name!r}. Try --list-folders.")
        if not folder.selectable():
            raise SystemExit(f"Folder is not selectable: {name!r}")
        targets.append(folder)
    return targets


@dataclass
class FolderPlan:
    folder: Folder
    spam_uids: list[bytes]            # union of provider + rule matches
    provider_spam_uids: list[bytes]
    rule_spam_uids: list[bytes]
    unread_uids: list[bytes]


def build_plans(
    conn: imaplib.IMAP4,
    folders: list[Folder],
    rules: SpamRules,
) -> list[FolderPlan]:
    plans = []
    for folder in folders:
        select_folder(conn, folder)
        provider_spam = find_provider_spam(conn)
        rule_spam = find_rule_spam(conn, rules)
        spam_uids = sorted(set(provider_spam) | set(rule_spam), key=int)
        unread_uids = sorted(uid_search(conn, "UNSEEN"), key=int)
        plans.append(
            FolderPlan(
                folder=folder,
                spam_uids=spam_uids,
                provider_spam_uids=provider_spam,
                rule_spam_uids=rule_spam,
                unread_uids=unread_uids,
            )
        )
    return plans


def print_samples(conn: imaplib.IMAP4, label: str, uids: list[bytes], count: int) -> None:
    if count <= 0 or not uids:
        return
    headers = fetch_headers(conn, uids[:count], ["From", "Subject", "Date", "X-Spam-Flag", "X-Spam-Status"])
    for uid in uids[:count]:
        payload = headers.get(uid)
        if payload is None:
            log(f"    - uid {uid.decode()}: (headers unavailable)")
            continue
        message = BytesHeaderParser(policy=policy.default).parsebytes(payload)
        sender = decode_header_value(message.get("From"))[:70]
        subject = decode_header_value(message.get("Subject"))[:90]
        log(f"    - uid {uid.decode():>8} | {sender} | {subject}")
    remaining = len(uids) - min(count, len(uids))
    if remaining > 0:
        log(f"    ... and {remaining} more")


def print_plan(conn: imaplib.IMAP4, plans: list[FolderPlan], args, rules: SpamRules) -> tuple[int, int]:
    total_spam = total_unread = 0
    log("")
    log("Plan")
    log("====")
    for plan in plans:
        spam = plan.spam_uids if not args.no_spam else []
        unread = plan.unread_uids if not args.no_unread else []
        total_spam += len(spam)
        total_unread += len(unread)
        log(f"\n[{plan.folder.name}]")
        if not args.no_spam:
            log(f"  Spam-header matches: {len(plan.provider_spam_uids)}")
            print_samples(conn, "spam", plan.provider_spam_uids, args.sample)
            if rules.active():
                log(f"  Rule matches: {len(plan.rule_spam_uids)}")
                print_samples(conn, "spam", plan.rule_spam_uids, args.sample)
        if not args.no_unread:
            log(f"  Unread messages: {len(unread)}")
            print_samples(conn, "unread", unread, args.sample)
    log("")
    log(f"Totals: {total_spam} spam candidate(s), {total_unread} unread message(s).")
    return total_spam, total_unread


# --------------------------------------------------------------------------- #
# Applying changes
# --------------------------------------------------------------------------- #

def open_audit_log(path: str):
    target = Path(path).expanduser()
    is_new = not target.exists()
    handle = target.open("a", newline="", encoding="utf-8")
    writer = csv.writer(handle)
    if is_new:
        writer.writerow(["timestamp", "action", "folder", "folder_raw", "uid"])
    return handle, writer


def write_audit(writer, action: str, folder: Folder, uids: list[bytes]) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    for uid in uids:
        writer.writerow([stamp, action, folder.name, folder.raw, uid.decode()])


def apply_plans(conn, plans, spam_folder, capabilities, args) -> tuple[int, int]:
    marked_read = 0
    moved = 0
    audit_handle = None
    audit_writer = None

    try:
        audit_handle, audit_writer = open_audit_log(args.audit_log)
        for plan in plans:
            folder = plan.folder
            log(f"\n[{folder.name}]")
            select_folder(conn, folder)

            if not args.no_spam and plan.spam_uids:
                targets = plan.spam_uids[:args.limit] if args.limit else plan.spam_uids
                # Set \Seen first: MOVE preserves flags, and even if the move
                # fails the message ends up read rather than lost.
                read_ok = mark_seen(conn, targets)
                if read_ok:
                    # Named distinctly from plain mark_read so --undo-read-audit
                    # does not try to "unread" messages that were also moved.
                    write_audit(audit_writer, "mark_read_for_spam", folder, targets[:read_ok])
                    marked_read += read_ok
                moved_ok = move_messages(conn, targets, spam_folder.raw, capabilities)
                if moved_ok:
                    # Recorded separately so an audit reader can tell why the
                    # message disappeared from this folder.
                    write_audit(audit_writer, "move_to_spam", folder, targets[:moved_ok])
                    moved += moved_ok
                log(f"  spam: moved {moved_ok}/{len(targets)} to {spam_folder.name}, marked read")
                audit_handle.flush()

            if not args.no_unread:
                # Re-search: messages moved above are gone and must not be
                # counted or touched again.
                unread = sorted(uid_search(conn, "UNSEEN"), key=int)
                targets = unread[:args.limit] if args.limit else unread
                if targets:
                    changed = mark_seen(conn, targets)
                    write_audit(audit_writer, "mark_read", folder, targets[:changed])
                    marked_read += changed
                    log(f"  unread: marked {changed}/{len(targets)} as read")
                    audit_handle.flush()
                else:
                    log("  unread: nothing to do")
    finally:
        if audit_handle:
            audit_handle.close()
    return marked_read, moved


# --------------------------------------------------------------------------- #
# Undo: restore read state recorded in an audit log
# --------------------------------------------------------------------------- #

def run_undo(args) -> int:
    path = Path(args.undo_read_audit).expanduser()
    if not path.is_file():
        log(f"Audit log not found: {path}", file=sys.stderr)
        return 2

    by_folder: dict[tuple[str, str], list[bytes]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("action") != "mark_read":
                continue
            raw = row.get("folder_raw") or row.get("folder") or DEFAULT_FOLDER
            display = row.get("folder") or raw
            by_folder.setdefault((raw, display), []).append(row["uid"].encode())

    total = sum(len(uids) for uids in by_folder.values())
    log(f"Audit log contains {total} mark_read action(s) in {len(by_folder)} folder(s).")
    for (raw, display), uids in by_folder.items():
        log(f"  {display}: {len(uids)} message(s)")
    if args.dry_run:
        log("\nDry run. Re-run without --dry-run to mark these messages unread again.")
        return 0
    if not total:
        return 0
    if not confirm(args):
        return 1

    auth = resolve_auth(args)
    conn = connect(auth, args.host, args.port)
    restored = 0
    try:
        for (raw, display), uids in by_folder.items():
            try:
                select_folder(conn, raw)
                restored += mark_unseen(conn, uids)
                log(f"  {display}: restored {len(uids)}")
            except Exception as exc:  # noqa: BLE001 - keep going per folder
                log(f"  {display}: failed - {exc}", file=sys.stderr)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    log(f"\nRestored {restored} message(s) to unread.")
    return 0


# --------------------------------------------------------------------------- #
# Connection / CLI
# --------------------------------------------------------------------------- #

def connect(auth: Auth, host: str, port: int) -> imaplib.IMAP4:
    context = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=60)

    if auth.mode == "oauth":
        try:
            from gmail_oauth import get_access_token
        except ImportError as exc:
            raise RuntimeError(str(exc)) from exc
        token = get_access_token(
            auth.credentials_path,
            auth.token_path,
            force=auth.reauth,
        )
        auth_string = f"user={auth.user}\x01auth=Bearer {token}\x01\x01"
        conn.authenticate("XOAUTH2", lambda _challenge: auth_string.encode("utf-8"))
        return conn

    if not auth.password:
        raise RuntimeError("App password is missing.")
    conn.login(auth.user, auth.password)
    return conn


def confirm(args) -> bool:
    if args.yes:
        return True
    answer = input("\nProceed? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        log("Aborted. No changes were made.")
        return False
    return True


def add_auth_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the sign-in/connection flags shared by all entry points."""
    parser.add_argument("--host", default=DEFAULT_HOST, help="IMAP host (default: imap.gmail.com)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="IMAP SSL port (default: 993)")
    parser.add_argument("--user", help="email address (or EMAIL_ADDRESS in .env/environment)")
    parser.add_argument("--password", help="app password (prefer .env or the prompt)")
    parser.add_argument("--auth", choices=("auto", "app-password", "oauth"), default="auto",
                        help="sign-in method (default: auto)")
    parser.add_argument("--credentials", default="credentials.json",
                        help="Google OAuth client file (default: credentials.json)")
    parser.add_argument("--token", default="token.json",
                        help="cached Google OAuth token (default: token.json)")
    parser.add_argument("--reauth", action="store_true",
                        help="ignore the cached OAuth token and sign in again")
    parser.add_argument("--env-file", help="path to .env (default: ./.env or script folder)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mark unread mail as read and move spam to Spam.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                          # prompt, then clean INBOX\n"
            "  %(prog)s --dry-run                # preview only, change nothing\n"
            "  %(prog)s --auth oauth             # sign in via the browser\n"
            "  %(prog)s --all-folders            # clean every non-spam folder\n"
            "  %(prog)s --limit 5                # touch at most 5 per folder/action\n"
            "  %(prog)s --spam-from newsletter@example.com --spam-subject 'You won'\n"
            "  %(prog)s --list-folders           # show folder names\n"
            "  %(prog)s --undo-read-audit cleanup-audit.csv\n"
        ),
    )
    add_auth_arguments(parser)
    parser.add_argument("--folder", action="append", metavar="NAME",
                        help="folder to clean; repeatable (default: INBOX)")
    parser.add_argument("--all-folders", action="store_true",
                        help="use every selectable folder except Spam/Trash/Drafts/Sent/All Mail")
    parser.add_argument("--spam-folder", help="override the detected Spam/Junk folder")
    parser.add_argument("--spam-from", action="append", metavar="TEXT",
                        help="move mail whose From header contains TEXT "
                             "(repeatable, case-insensitive)")
    parser.add_argument("--spam-subject", action="append", metavar="TEXT",
                        help="move mail whose Subject contains TEXT "
                             "(repeatable, case-insensitive)")
    parser.add_argument("--spam-contains", action="append", metavar="TEXT",
                        help="move mail whose From or Subject contains TEXT "
                             "(repeatable, case-insensitive)")
    parser.add_argument("--list-folders", action="store_true", help="list folders and exit")
    parser.add_argument("--no-spam", action="store_true", help="skip moving spam")
    parser.add_argument("--no-unread", action="store_true", help="skip marking unread as read")
    parser.add_argument("--limit", type=int, help="max messages per folder per action")
    parser.add_argument("--sample", type=int, default=5,
                        help="messages to preview per category (default: 5, 0 disables)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would happen without changing anything")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--audit-log", default="cleanup-audit.csv",
                        help="CSV file for an audit trail (default: cleanup-audit.csv)")
    parser.add_argument("--undo-read-audit", metavar="CSV",
                        help="mark messages listed as mark_read in an audit log as unread again")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    if args.undo_read_audit:
        try:
            return run_undo(args)
        except imaplib.IMAP4.error as exc:
            log(f"IMAP error: {exc}", file=sys.stderr)
            return 2
        except (RuntimeError, OSError) as exc:
            log(str(exc), file=sys.stderr)
            return 2

    auth = resolve_auth(args)

    try:
        conn = connect(auth, args.host, args.port)
    except imaplib.IMAP4.error as exc:
        log(f"Login failed: {exc}", file=sys.stderr)
        log("", file=sys.stderr)
        if auth.mode == "app-password":
            log("Gmail app passwords require 2-Step Verification:", file=sys.stderr)
            log("  Google Account > Security > App passwords", file=sys.stderr)
            log("Regular account passwords do not work with IMAP.", file=sys.stderr)
            log("Or sign in through the browser with: --auth oauth", file=sys.stderr)
        else:
            log("The Google token was rejected. Try again with --reauth.", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        log(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        log(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        log(f"Could not reach {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 2

    try:
        folders = list_folders(conn)
        if args.list_folders:
            log(f"Folders for {auth.user}:")
            for folder in folders:
                flags = " ".join(sorted(folder.flags)) or "-"
                log(f"  {folder.name:<35} {flags}")
            return 0

        spam_folder = pick_spam_folder(folders, args.spam_folder)
        targets = choose_target_folders(folders, args.folder, args.all_folders, spam_folder)
        capabilities = server_capabilities(conn)
        rules = SpamRules.from_args(args)

        log(f"Connected as {auth.user} ({args.host})")
        log(f"Spam folder: {spam_folder.name}")
        log(f"Target folder(s): {', '.join(folder.name for folder in targets)}")
        if rules.active():
            log(
                "Spam rules: "
                + "; ".join(
                    f"{label}={len(patterns)}"
                    for label, patterns in (
                        ("from", rules.from_patterns),
                        ("subject", rules.subject_patterns),
                        ("contains", rules.contains_patterns),
                    )
                    if patterns
                )
            )

        plans = build_plans(conn, targets, rules)
        total_spam, total_unread = print_plan(conn, plans, args, rules)

        if args.no_spam and args.no_unread:
            log("Both actions are disabled; nothing to do.")
            return 0

        if args.dry_run:
            log("\nDry run. No changes made. Re-run without --dry-run to execute.")
            return 0

        if total_spam == 0 and total_unread == 0:
            log("\nNothing to do.")
            return 0

        if not confirm(args):
            return 1

        marked_read, moved = apply_plans(conn, plans, spam_folder, capabilities, args)
        log("")
        log(f"Done. Marked {marked_read} message(s) as read; moved {moved} to {spam_folder.name}.")
        log(f"Audit log: {Path(args.audit_log).expanduser().resolve()}")
        return 0
    finally:
        try:
            conn.logout()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
