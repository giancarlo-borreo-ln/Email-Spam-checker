"""In-memory IMAP4 server double used by the test suite."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime

STANDARD_FOLDERS = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"',
    b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
    b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"',
    b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
    b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"',
]


def unquote(name: str) -> str:
    if name.startswith('"') and name.endswith('"'):
        return name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return name


@dataclass(slots=True)
class Message:
    header: bytes
    flags: set[str] = field(default_factory=set)
    internal_date: str | None = None


def make_header(
    index: int,
    *,
    spam_flag: str | None = None,
    spam_status: str | None = None,
    sender: str | None = None,
    subject: str | None = None,
) -> bytes:
    lines = [
        f"From: {sender or f'sender{index}@example.test'}",
        f"Subject: {subject or f'Message {index}'}",
        "Date: Mon, 19 Oct 2026 10:00:00 +0000",
    ]
    if spam_flag is not None:
        lines.append(f"X-Spam-Flag: {spam_flag}")
    if spam_status is not None:
        lines.append(f"X-Spam-Status: {spam_status}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def _header_date(header: bytes):
    raw = parse_header_fields(header).get(b"date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw.decode("ascii", "replace")).date()
    except (TypeError, ValueError, OverflowError):
        return None


def parse_header_fields(header: bytes) -> dict[bytes, bytes]:
    """Minimal header parser: lowercased field name -> folded value."""
    result: dict[bytes, bytes] = {}
    current: bytes | None = None
    for raw_line in header.split(b"\n"):
        line = raw_line.rstrip(b"\r")
        if not line:
            current = None
            continue
        if line[:1] in (b" ", b"\t"):
            if current is not None:
                result[current] += b" " + line.strip()
            continue
        name, separator, value = line.partition(b":")
        if not separator:
            current = None
            continue
        current = name.strip().lower()
        result[current] = value.strip()
    return result


class FakeIMAP:
    def __init__(
        self,
        folders: list[bytes],
        messages: dict[str, dict[bytes, Message]],
        capabilities: bytes = b"IMAP4REV1 MOVE UIDPLUS",
    ):
        self._folders = list(folders)
        self.messages = messages
        self.capability_blob = capabilities
        self.capabilities = tuple(part.decode() for part in capabilities.split())
        self.selected: str | None = None
        self.commands: list[tuple[str, tuple]] = []
        self.uid_set_sizes: list[tuple[str, int]] = []
        self.selected_history: list[str] = []
        self.authenticated: tuple[str, bytes] | None = None
        self.logged_in: tuple[str, str] | None = None
        self.logged_out = False
        self.expunge_calls = 0
        self.last_readonly = False
        self.readonly_history: list[tuple[str, bool]] = []
        self._next_uid = {
            name: (max((int(uid) for uid in msgs), default=0) + 1)
            for name, msgs in messages.items()
        }

    # -- commands ---------------------------------------------------------- #

    def list(self):
        return "OK", list(self._folders)

    def capability(self):
        return "OK", [self.capability_blob]

    def select(self, name, readonly=False):
        name = unquote(name)
        if name not in self.messages:
            return "NO", [b"no such mailbox"]
        self.selected = name
        self.last_readonly = readonly
        self.readonly_history.append((name, readonly))
        self.selected_history.append(name)
        return "OK", [str(len(self.messages[name])).encode()]

    def login(self, user, password):
        self.logged_in = (user, password)
        return "OK", [b""]

    def authenticate(self, mechanism, callback):
        self.authenticated = (mechanism, callback(None))
        return "OK", [b""]

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]

    def expunge(self):
        self.expunge_calls += 1
        mailbox = self.messages[self.selected]
        removed = [uid for uid, msg in mailbox.items() if "\\Deleted" in msg.flags]
        for uid in removed:
            del mailbox[uid]
        return "OK", [b""]

    def uid(self, command, *args):
        command = command.upper()
        self.commands.append((command, args))
        mailbox = self.messages[self.selected] if self.selected else {}

        if command == "SEARCH":
            return self._search(mailbox, args[1:])
        if command == "FETCH":
            return self._fetch(mailbox, args[0], args[1] if len(args) > 1 else "")
        if command == "STORE":
            return self._store(mailbox, args[0], args[1], args[2])
        if command == "MOVE":
            return self._move(mailbox, args[0], args[1])
        if command == "COPY":
            return self._copy(mailbox, args[0], args[1])
        if command == "EXPUNGE":
            return self._uid_expunge(mailbox, args[0])
        return "BAD", [b"unsupported"]

    # -- internals --------------------------------------------------------- #

    @staticmethod
    def _uids(value) -> list[bytes]:
        if isinstance(value, str):
            value = value.encode()
        return [part for part in value.split(b",") if part]

    def _record_uids(self, command: str, uids: list[bytes]) -> None:
        self.uid_set_sizes.append((command, len(uids)))

    def _search(self, mailbox, criteria):
        criteria = tuple(criteria)
        matches = []
        for uid, message in sorted(mailbox.items(), key=lambda item: int(item[0])):
            if criteria == ("UNSEEN",):
                if "\\Seen" not in message.flags:
                    matches.append(uid)
            elif criteria == ("ALL",):
                matches.append(uid)
            elif criteria and str(criteria[0]).upper() in ("SENTSINCE", "SENTBEFORE"):
                since = before = None
                position = 0
                while position + 1 < len(criteria):
                    key = str(criteria[position]).upper()
                    value = str(criteria[position + 1])
                    parsed = datetime.strptime(value, "%d-%b-%Y").date()
                    if key == "SENTSINCE":
                        since = parsed
                    elif key == "SENTBEFORE":
                        before = parsed
                    position += 2
                sent_date = _header_date(message.header)
                if sent_date is not None:
                    if (since is None or sent_date >= since) and (
                        before is None or sent_date < before
                    ):
                        matches.append(uid)
            elif len(criteria) == 3 and str(criteria[0]).upper() == "HEADER":
                field = str(criteria[1]).lower().encode()
                needle = str(criteria[2]).lower().encode()
                value = parse_header_fields(message.header).get(field, b"").lower()
                if needle in value:
                    matches.append(uid)
            elif len(criteria) == 2 and str(criteria[0]).upper() in ("FROM", "SUBJECT"):
                field = str(criteria[0]).lower().encode()
                needle = str(criteria[1])
                if needle.startswith('"') and needle.endswith('"'):
                    needle = needle[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                needle = needle.lower().encode()
                value = parse_header_fields(message.header).get(field, b"").lower()
                if needle in value:
                    matches.append(uid)
            else:
                raise AssertionError(f"unsupported SEARCH criteria: {criteria!r}")
        return "OK", [b" ".join(matches)]

    def _fetch(self, mailbox, uid_arg, spec):
        uids = self._uids(uid_arg)
        self._record_uids("FETCH", uids)

        if "INTERNALDATE" in spec.upper() and "HEADER.FIELDS" not in spec.upper():
            response = []
            for uid in uids:
                message = mailbox.get(uid)
                if message is None:
                    continue
                internal = message.internal_date or "01-Jan-2000 00:00:00 +0000"
                response.append(
                    b"1 (UID " + uid + b' INTERNALDATE "' + internal.encode() + b'")'
                )
            return "OK", response

        response = []
        for uid in uids:
            message = mailbox.get(uid)
            if message is None:
                continue
            internal = b""
            if "INTERNALDATE" in spec.upper():
                internal_date = message.internal_date or "01-Jan-2000 00:00:00 +0000"
                internal = b' INTERNALDATE "' + internal_date.encode() + b'"'
            response.append(
                (
                    b"1 (UID " + uid + internal + b" BODY[HEADER.FIELDS (...)] {"
                    + str(len(message.header)).encode() + b"}",
                    message.header,
                )
            )
            response.append(b")")
        return "OK", response

    def _store(self, mailbox, uid_arg, operation, flag_arg):
        uids = self._uids(uid_arg)
        self._record_uids("STORE", uids)
        changes = {flag for flag in re.findall(r"\\([A-Za-z]+)", flag_arg)}
        changes = {"\\" + flag for flag in changes}
        for uid in uids:
            message = mailbox.get(uid)
            if message is None:
                continue
            if operation.startswith("+"):
                message.flags |= changes
            elif operation.startswith("-"):
                message.flags -= changes
            else:
                message.flags = set(changes)
        return "OK", [b""]

    def _move(self, mailbox, uid_arg, destination):
        destination = unquote(destination)
        uids = self._uids(uid_arg)
        self._record_uids("MOVE", uids)
        target = self.messages[destination]
        for uid in uids:
            message = mailbox.pop(uid, None)
            if message is None:
                continue
            new_uid = str(self._next_uid[destination]).encode()
            self._next_uid[destination] += 1
            target[new_uid] = message
        return "OK", [b""]

    def _copy(self, mailbox, uid_arg, destination):
        destination = unquote(destination)
        uids = self._uids(uid_arg)
        self._record_uids("COPY", uids)
        target = self.messages[destination]
        for uid in uids:
            message = mailbox.get(uid)
            if message is None:
                continue
            new_uid = str(self._next_uid[destination]).encode()
            self._next_uid[destination] += 1
            target[new_uid] = Message(
                header=message.header,
                flags=set(message.flags),
                internal_date=message.internal_date,
            )
        return "OK", [b""]

    def _uid_expunge(self, mailbox, uid_arg):
        uids = self._uids(uid_arg)
        self._record_uids("EXPUNGE", uids)
        for uid in uids:
            message = mailbox.get(uid)
            if message is not None and "\\Deleted" in message.flags:
                del mailbox[uid]
        return "OK", [b""]
