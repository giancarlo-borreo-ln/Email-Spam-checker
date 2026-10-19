#!/usr/bin/env python3
"""
mailbox_stats.py - read-only mailbox statistics.

Reports how many messages each folder has, how many are unread, and finds the
oldest messages you sent and received.

Ordering uses the RFC 5322 `Date:` header (the date shown in Gmail), with the
server INTERNALDATE as a fallback for malformed headers.

This script never changes anything: it opens folders with EXAMINE (read-only)
and only runs SEARCH/FETCH.
"""

from __future__ import annotations

import argparse
import imaplib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesHeaderParser
from email.utils import parsedate_to_datetime

import cleanup_mail as cm

UID_RE = re.compile(rb"UID (\d+)")
INTERNALDATE_RE = re.compile(rb'INTERNALDATE "([^"]+)"')
HEADER_FIELDS = ["Date", "From", "To", "Subject", "Message-ID"]


@dataclass
class FolderCount:
    label: str
    folder: cm.Folder | None
    total: int = 0
    unread: int = 0


@dataclass
class MessageInfo:
    uid: str
    date: str | None
    date_source: str
    internal_date: str | None
    from_: str
    to: str
    subject: str
    message_id: str


def find_folder(
    folders: list[cm.Folder],
    flags: tuple[str, ...] = (),
    names: tuple[str, ...] = (),
) -> cm.Folder | None:
    """Find a folder by special-use flag first, then by name."""
    flag_set = {flag.upper() for flag in flags}
    for folder in folders:
        if flag_set and folder.flags & flag_set:
            return folder
    by_name = {folder.name.strip().lower(): folder for folder in folders}
    for name in names:
        found = by_name.get(name.lower())
        if found:
            return found
    return None


def count_folder(conn: imaplib.IMAP4, folder: cm.Folder) -> tuple[int, int]:
    total = cm.select_folder(conn, folder, readonly=True)
    unread = len(cm.uid_search(conn, "UNSEEN"))
    return total, unread


def parse_internal_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


def parse_date_header(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def fetch_metadata(
    conn: imaplib.IMAP4,
    uids: list[bytes],
    chunk_size: int = 200,
) -> list[MessageInfo]:
    """Fetch Date/From/To/Subject for UIDs, in batches."""
    infos: list[MessageInfo] = []
    for chunk in cm.chunked(uids, chunk_size):
        status, data = conn.uid(
            "FETCH",
            cm.uid_set(chunk),
            "(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS ("
            + " ".join(HEADER_FIELDS)
            + ")])",
        )
        if status != "OK":
            continue
        for item in data or []:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            meta, payload = item
            uid_match = UID_RE.search(meta)
            if not uid_match:
                continue
            internal_match = INTERNALDATE_RE.search(meta)
            internal = (
                parse_internal_date(internal_match.group(1).decode("ascii", "replace"))
                if internal_match
                else None
            )

            message = BytesHeaderParser(policy=policy.default).parsebytes(payload)
            date = parse_date_header(message.get("Date"))
            source = "Date header"
            if date is None and internal is not None:
                date = internal
                source = "INTERNALDATE"

            infos.append(
                MessageInfo(
                    uid=uid_match.group(1).decode(),
                    date=date.isoformat() if date else None,
                    date_source=source,
                    internal_date=internal.isoformat() if internal else None,
                    from_=cm.decode_header_value(message.get("From")),
                    to=cm.decode_header_value(message.get("To")),
                    subject=cm.decode_header_value(message.get("Subject")),
                    message_id=cm.decode_header_value(message.get("Message-ID")),
                )
            )
    return infos


def _has_message_before(conn: imaplib.IMAP4, imap_date: str) -> bool:
    return bool(cm.uid_search(conn, "SENTBEFORE", imap_date))


def earliest_year(conn: imaplib.IMAP4, start_year: int = 1900) -> int | None:
    """Find the first year containing a message, using Date headers."""
    end_year = datetime.now(timezone.utc).year + 1
    if not _has_message_before(conn, f"1-Jan-{end_year + 1}"):
        return None

    low, high = start_year, end_year
    while low < high:
        middle = (low + high) // 2
        if _has_message_before(conn, f"1-Jan-{middle + 1}"):
            high = middle
        else:
            low = middle + 1
    return low


def sort_key(info: MessageInfo) -> tuple[datetime, int]:
    if info.date:
        parsed = datetime.fromisoformat(info.date)
    else:
        parsed = datetime.max.replace(tzinfo=timezone.utc)
    return parsed, int(info.uid)


def find_oldest(
    conn: imaplib.IMAP4,
    folder: cm.Folder,
    count: int = 5,
    *,
    exclude_address: str | None = None,
) -> list[MessageInfo]:
    """Return the oldest messages by Date header, optionally excluding a sender."""
    cm.select_folder(conn, folder, readonly=True)
    if not cm.uid_search(conn, "ALL"):
        return []

    year = earliest_year(conn)
    if year is None:
        return []

    current_year = datetime.now(timezone.utc).year + 1
    found: list[MessageInfo] = []
    scanned_years = 0

    while year <= current_year and len(found) < count and scanned_years < 10:
        uids = cm.uid_search(
            conn,
            "SENTSINCE",
            f"1-Jan-{year}",
            "SENTBEFORE",
            f"1-Jan-{year + 1}",
        )
        if uids:
            infos = fetch_metadata(conn, uids)
            if exclude_address:
                needle = exclude_address.lower()
                infos = [info for info in infos if needle not in info.from_.lower()]
            found.extend(infos)

        year += 1
        scanned_years += 1

    found.sort(key=sort_key)
    return found[:count]


def build_report(
    conn: imaplib.IMAP4,
    folders: list[cm.Folder],
    user: str,
    oldest_count: int,
    include_oldest: bool = True,
) -> dict:
    roles = [
        ("All Mail", find_folder(folders, ("\\ALL",), ("All Mail", "[Gmail]/All Mail"))),
        ("INBOX", find_folder(folders, names=("INBOX",))),
        ("Sent Mail", find_folder(folders, ("\\SENT",), ("Sent Mail", "Sent", "[Gmail]/Sent Mail"))),
        ("Drafts", find_folder(folders, ("\\DRAFTS",), ("Drafts", "[Gmail]/Drafts"))),
        ("Spam", find_folder(folders, ("\\JUNK", "\\SPAM"), ("Spam", "Junk", "[Gmail]/Spam"))),
        ("Trash", find_folder(folders, ("\\TRASH",), ("Trash", "[Gmail]/Trash"))),
    ]

    counts = []
    for label, folder in roles:
        if folder is None:
            counts.append(FolderCount(label=label, folder=None))
            continue
        total, unread = count_folder(conn, folder)
        counts.append(FolderCount(label=label, folder=folder, total=total, unread=unread))

    sent = find_folder(folders, ("\\SENT",), ("Sent Mail", "Sent", "[Gmail]/Sent Mail"))
    all_mail = find_folder(folders, ("\\ALL",), ("All Mail", "[Gmail]/All Mail"))
    inbox = find_folder(folders, names=("INBOX",))

    oldest_sent = (
        find_oldest(conn, sent, oldest_count) if include_oldest and sent else []
    )
    received_folder = all_mail or inbox
    oldest_received = (
        find_oldest(conn, received_folder, oldest_count, exclude_address=user)
        if include_oldest and received_folder
        else []
    )

    return {
        "folders": counts,
        "oldest_sent": oldest_sent,
        "oldest_received": oldest_received,
    }


def format_date(info: MessageInfo) -> str:
    if not info.date:
        return "unknown"
    parsed = datetime.fromisoformat(info.date)
    return parsed.strftime("%Y-%m-%d %H:%M:%S %z")


def print_report(user: str, report: dict) -> None:
    counts: list[FolderCount] = report["folders"]
    print(f"Mailbox statistics for {user}")
    print("=" * (24 + len(user)))
    print(f"{'Mailbox':<22}{'Messages':>12}{'Unread':>12}")
    print("-" * 46)
    for row in counts:
        if row.folder is None:
            print(f"{row.label:<22}{'-':>12}{'-':>12}")
        else:
            print(f"{row.label:<22}{row.total:>12,}{row.unread:>12,}")
    print()
    print("All Mail is the total unique messages. Labels overlap, and All Mail")
    print("excludes Spam and Trash, so the other rows do not add up to it.")


def print_message_list(title: str, infos: list[MessageInfo], *, show_from: bool) -> None:
    print(title)
    print("-" * len(title))
    if not infos:
        print("  (none found)")
        return
    for info in infos:
        who = f"From: {info.from_}" if show_from else f"To: {info.to}"
        print(f"  {format_date(info)} | {who}")
        print(f"    Subject: {info.subject or '(no subject)'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Count messages and find the first messages you sent and received.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                      # counts + oldest sent/received\n"
            "  %(prog)s --json               # machine-readable output\n"
            "  %(prog)s --oldest 10          # show 10 examples per direction\n"
            "  %(prog)s --no-first-sent      # skip the Sent Mail scan\n"
        ),
    )
    cm.add_auth_arguments(parser)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--oldest", type=int, default=5,
                        help="how many of the oldest messages to list (default: 5)")
    parser.add_argument("--no-first-sent", "--no-oldest", dest="no_oldest",
                        action="store_true",
                        help="skip scanning for the oldest sent/received messages")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.oldest < 1:
        print("--oldest must be at least 1", file=sys.stderr)
        return 2

    try:
        auth = cm.resolve_auth(args)
        conn = cm.connect(auth, args.host, args.port)
    except imaplib.IMAP4.error as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        print("If this is Gmail, use --auth oauth or a Google app password.", file=sys.stderr)
        return 2
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        folders = cm.list_folders(conn)
        report = build_report(
            conn,
            folders,
            auth.user,
            args.oldest,
            include_oldest=not args.no_oldest,
        )
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    if args.json:
        print(json.dumps(
            {
                "user": auth.user,
                "folders": [
                    {
                        "label": row.label,
                        "folder": row.folder.name if row.folder else None,
                        "messages": row.total if row.folder else None,
                        "unread": row.unread if row.folder else None,
                    }
                    for row in report["folders"]
                ],
                "first_sent": asdict(report["oldest_sent"][0]) if report["oldest_sent"] else None,
                "oldest_sent": [asdict(info) for info in report["oldest_sent"]],
                "first_received": (
                    asdict(report["oldest_received"][0]) if report["oldest_received"] else None
                ),
                "oldest_received": [asdict(info) for info in report["oldest_received"]],
            },
            indent=2,
        ))
    else:
        print_report(auth.user, report)
        print()
        print_message_list("Oldest messages you sent", report["oldest_sent"], show_from=False)
        print()
        print_message_list("Oldest messages you received", report["oldest_received"], show_from=True)
        print()
        print("Dates use the message Date: header (falling back to INTERNALDATE).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
