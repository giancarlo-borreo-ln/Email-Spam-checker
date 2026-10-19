"""Unit and integration tests for cleanup_mail.py."""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import cleanup_mail as cm
from tests.fake_imap import (
    STANDARD_FOLDERS,
    FakeIMAP,
    Message,
    make_header,
)

DEFAULT_AUTH = cm.Auth(user="me@example.test", mode="app-password", password="secret")


def build_inbox(
    count: int,
    *,
    spam: set[int] | None = None,
    unread: set[int] | None = None,
) -> dict[bytes, Message]:
    """Create `count` messages with 1-based UIDs; i is the 0-based index."""
    spam = spam or set()
    mailbox = {}
    for index in range(count):
        if index in spam:
            header = make_header(
                index,
                spam_flag="YES" if index % 2 == 0 else None,
                spam_status=None if index % 2 == 0 else "Yes, score=9.0 required=5.0",
            )
        else:
            header = make_header(index)
        is_unread = unread is None or index in unread
        mailbox[str(index + 1).encode()] = Message(
            header=header,
            flags=set() if is_unread else {"\\Seen"},
        )
    return mailbox


def run_cleanup(fake: FakeIMAP, argv: list[str], auth: cm.Auth | None = None):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        patch.object(cm, "resolve_auth", return_value=auth or DEFAULT_AUTH),
        patch.object(cm, "connect", return_value=fake),
        patch.object(cm, "PAUSE", 0),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        code = cm.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def count_spam_messages(messages: dict[bytes, Message]) -> int:
    return sum(
        1
        for message in messages.values()
        if b"X-Spam-Flag: YES" in message.header or b"X-Spam-Status: Yes" in message.header
    )


class HelperTests(unittest.TestCase):
    def test_quote_mailbox(self):
        self.assertEqual(cm.quote_mailbox("[Gmail]/Spam"), '"[Gmail]/Spam"')
        self.assertEqual(cm.quote_mailbox('weird"name'), '"weird\\"name"')

    def test_quote_search(self):
        self.assertEqual(cm.quote_search("plain"), '"plain"')
        self.assertEqual(cm.quote_search('a"b\\c'), '"a\\"b\\\\c"')

    def test_decode_imap_utf7(self):
        self.assertEqual(cm.decode_imap_utf7("INBOX"), "INBOX")
        self.assertEqual(cm.decode_imap_utf7("&AOk-tudes"), "études")
        self.assertEqual(cm.decode_imap_utf7("A&-B"), "A&B")

    def test_parse_folder_entry(self):
        folder = cm.parse_folder_entry(b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"')
        self.assertIsNotNone(folder)
        assert folder is not None
        self.assertEqual(folder.raw, "[Gmail]/Spam")
        self.assertEqual(folder.name, "[Gmail]/Spam")
        self.assertIn("\\JUNK", folder.flags)
        self.assertTrue(folder.selectable())

        noselect = cm.parse_folder_entry(b'(\\Noselect \\HasChildren) "/" "[Gmail]"')
        assert noselect is not None
        self.assertFalse(noselect.selectable())

        quoted = cm.parse_folder_entry(b'(\\HasNoChildren) "/" "My \\"Quoted\\" Folder"')
        assert quoted is not None
        self.assertEqual(quoted.raw, 'My "Quoted" Folder')

        self.assertIsNone(cm.parse_folder_entry(b"garbage"))

    def test_chunked(self):
        self.assertEqual(list(cm.chunked(list(range(5)), 2)), [[0, 1], [2, 3], [4]])
        self.assertEqual(list(cm.chunked([], 10)), [])

    def test_is_header_spam(self):
        yes_flag = b"X-Spam-Flag: YES\r\n\r\n"
        yes_status = b"X-Spam-Status: Yes, score=7.1\r\n\r\n"
        folded = b"X-Spam-Status: Yes,\r\n\tscore=7.1\r\n\r\n"
        no_flag = b"X-Spam-Flag: NO\r\nX-Spam-Status: No, score=-1\r\n\r\n"
        missing = b"Subject: hello\r\n\r\n"

        self.assertTrue(cm.is_header_spam(yes_flag))
        self.assertTrue(cm.is_header_spam(yes_status))
        self.assertTrue(cm.is_header_spam(folded))
        self.assertFalse(cm.is_header_spam(no_flag))
        self.assertFalse(cm.is_header_spam(missing))

    def test_load_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# comment\nEMAIL_ADDRESS=me@example.test\n"
                "EMAIL_APP_PASSWORD='space pw'\n\nOTHER=\"x\"\n",
                encoding="utf-8",
            )
            values = cm.load_env_file(path)
        self.assertEqual(values["EMAIL_ADDRESS"], "me@example.test")
        self.assertEqual(values["EMAIL_APP_PASSWORD"], "space pw")
        self.assertEqual(values["OTHER"], "x")

    def test_pick_spam_folder_variants(self):
        folders = [
            cm.parse_folder_entry(line) for line in STANDARD_FOLDERS
        ]
        folders = [folder for folder in folders if folder]
        self.assertEqual(cm.pick_spam_folder(folders, None).raw, "[Gmail]/Spam")

        localized = [cm.Folder(raw="Posta indesiderata", name="Posta indesiderata", flags=frozenset())]
        self.assertEqual(cm.pick_spam_folder(localized, None).raw, "Posta indesiderata")

        override = cm.pick_spam_folder(folders, "INBOX")
        self.assertEqual(override.raw, "INBOX")

    def test_all_folders_excludes_special_and_spam(self):
        folders = [cm.parse_folder_entry(line) for line in STANDARD_FOLDERS]
        folders = [folder for folder in folders if folder]
        folders.append(cm.Folder(raw="Projects", name="Projects", flags=frozenset()))
        spam = cm.pick_spam_folder(folders, None)
        targets = cm.choose_target_folders(folders, None, True, spam)
        names = [folder.raw for folder in targets]
        self.assertEqual(names, ["INBOX", "Projects"])

    def test_choose_target_folder_missing(self):
        folders = [cm.parse_folder_entry(b'(\\HasNoChildren) "/" "INBOX"')]
        folders = [folder for folder in folders if folder]
        spam = cm.Folder(raw="[Gmail]/Spam", name="[Gmail]/Spam", flags=frozenset())
        with self.assertRaises(SystemExit):
            cm.choose_target_folders(folders, ["Nope"], False, spam)

    def test_choose_auth_mode_explicit(self):
        args = cm.build_parser().parse_args([])
        args.auth = "oauth"
        self.assertEqual(cm.choose_auth_mode(args), "oauth")
        args.auth = "app-password"
        self.assertEqual(cm.choose_auth_mode(args), "app-password")

    def test_choose_auth_mode_auto_detects_oauth_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "token.json"
            token.write_text("{}", encoding="utf-8")
            args = cm.build_parser().parse_args(["--token", str(token)])
            self.assertEqual(cm.choose_auth_mode(args), "oauth")

    def test_resolve_auth_prompts_for_email_and_password(self):
        args = cm.build_parser().parse_args(["--auth", "app-password"])
        with (
            patch.object(cm, "resolve_env_file", return_value=None),
            patch("builtins.input", return_value="me@example.test"),
            patch("cleanup_mail.getpass.getpass", return_value="app-pw"),
        ):
            auth = cm.resolve_auth(args)
        self.assertEqual(auth.user, "me@example.test")
        self.assertEqual(auth.mode, "app-password")
        self.assertEqual(auth.password, "app-pw")

    def test_oauth_without_credentials_exits_with_help(self):
        args = cm.build_parser().parse_args(
            ["--auth", "oauth", "--credentials", "/definitely/missing.json",
             "--token", "/definitely/missing-token.json"]
        )
        with (
            patch.object(cm, "resolve_env_file", return_value=None),
            patch("builtins.input", return_value="me@example.test"),
            self.assertRaises(SystemExit) as ctx,
        ):
            cm.resolve_auth(args)
        self.assertIn("credentials.json", str(ctx.exception))


class SmallMailboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audit = str(Path(self.tmp.name) / "audit.csv")

    def make_fake(self, inbox, spam=None, **kwargs):
        return FakeIMAP(
            STANDARD_FOLDERS,
            {
                "INBOX": inbox,
                "[Gmail]/Spam": spam or {},
                "[Gmail]/Sent Mail": {},
                "[Gmail]/All Mail": {},
                "[Gmail]/Trash": {},
                "[Gmail]/Drafts": {},
            },
            **kwargs,
        )

    def test_dry_run_changes_nothing(self):
        inbox = build_inbox(6, spam={1, 2, 4}, unread={0, 1, 2, 5})
        fake = self.make_fake(inbox)
        before = {uid: (msg.header, set(msg.flags)) for uid, msg in inbox.items()}

        code, out, _ = run_cleanup(fake, ["--dry-run", "--audit-log", self.audit])

        self.assertEqual(code, 0)
        self.assertIn("Dry run", out)
        self.assertIn("Spam-header matches: 3", out)
        self.assertEqual(
            {uid: (msg.header, msg.flags) for uid, msg in fake.messages["INBOX"].items()},
            before,
        )
        self.assertEqual(fake.messages["[Gmail]/Spam"], {})
        self.assertFalse(Path(self.audit).exists())

    def test_apply_moves_spam_and_marks_unread(self):
        inbox = build_inbox(
            6,
            spam={1, 2, 4},
            unread={0, 1, 2, 5},  # 4 (spam) is already read
        )
        fake = self.make_fake(inbox, spam={b"100": Message(make_header(99), {"\\Seen"})})

        code, out, _ = run_cleanup(fake, ["--yes", "--audit-log", self.audit])

        self.assertEqual(code, 0)
        self.assertIn("moved 3", out)
        self.assertEqual(set(fake.messages["INBOX"]), {b"1", b"4", b"6"})
        self.assertEqual(len(fake.messages["[Gmail]/Spam"]), 4)
        for message in fake.messages["INBOX"].values():
            self.assertIn("\\Seen", message.flags)
        self.assertIn("Marked 5", out)

        with open(self.audit, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        actions = [row["action"] for row in rows]
        self.assertEqual(actions.count("move_to_spam"), 3)
        self.assertEqual(actions.count("mark_read_for_spam"), 3)
        self.assertEqual(actions.count("mark_read"), 2)
        self.assertTrue(all(row["folder_raw"] == "INBOX" for row in rows))

    def test_decoy_spam_headers_are_not_moved(self):
        decoy = Message(make_header(0, spam_flag="NO", spam_status="No, score=-1.0"), set())
        inbox = {b"1": decoy, b"2": Message(make_header(1), set())}
        fake = self.make_fake(inbox)

        code, out, _ = run_cleanup(fake, ["--yes", "--audit-log", self.audit])

        self.assertEqual(code, 0)
        self.assertIn("Spam-header matches: 0", out)
        self.assertEqual(set(fake.messages["INBOX"]), {b"1", b"2"})
        self.assertEqual(fake.messages["[Gmail]/Spam"], {})

    def test_rule_spam_moves_from_subject_and_contains_matches(self):
        inbox = {
            b"1": Message(make_header(0, sender="news@spammy.test"), set()),
            b"2": Message(make_header(1, subject="You WON a prize"), set()),
            b"3": Message(make_header(2, sender="friend@example.test", subject="hello"), set()),
            b"4": Message(make_header(3, subject="Crypto offer inside"), set()),
            b"5": Message(make_header(4, sender="news@spammy.test", subject="Crypto offer"), set()),
        }
        fake = self.make_fake(inbox)

        code, out, _ = run_cleanup(
            fake,
            [
                "--yes",
                "--sample", "0",
                "--spam-from", "SPAMMY.TEST",
                "--spam-subject", "you won",
                "--spam-contains", "crypto",
                "--audit-log", self.audit,
            ],
        )

        self.assertEqual(code, 0)
        # UID 3 is the only non-match; UID 5 matches twice but must move once.
        self.assertEqual(set(fake.messages["INBOX"]), {b"3"})
        self.assertEqual(len(fake.messages["[Gmail]/Spam"]), 4)
        self.assertIn("Rule matches: 4", out)

        with open(self.audit, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        moved = [row["uid"] for row in rows if row["action"] == "move_to_spam"]
        self.assertEqual(moved, ["1", "2", "4", "5"])

    def test_rule_does_not_match_other_headers_or_body(self):
        inbox = {
            b"1": Message(make_header(0, sender="ok@example.test", subject="spam rules"), set()),
        }
        fake = self.make_fake(inbox)

        code, out, _ = run_cleanup(
            fake,
            ["--yes", "--spam-from", "spam", "--audit-log", self.audit],
        )

        self.assertEqual(code, 0)
        self.assertEqual(set(fake.messages["INBOX"]), {b"1"})
        self.assertEqual(fake.messages["[Gmail]/Spam"], {})
        self.assertIn("Rule matches: 0", out)

    def test_rule_dry_run_moves_nothing(self):
        inbox = {b"1": Message(make_header(0, sender="news@spammy.test"), set())}
        fake = self.make_fake(inbox)

        code, out, _ = run_cleanup(
            fake,
            ["--dry-run", "--spam-from", "spammy.test", "--audit-log", self.audit],
        )

        self.assertEqual(code, 0)
        self.assertIn("Rule matches: 1", out)
        self.assertEqual(set(fake.messages["INBOX"]), {b"1"})
        self.assertEqual(fake.messages["[Gmail]/Spam"], {})
        self.assertFalse(Path(self.audit).exists())

    def test_no_spam_skips_moves_but_marks_read(self):
        inbox = build_inbox(4, spam={1}, unread={0, 1, 2})
        fake = self.make_fake(inbox)

        code, out, _ = run_cleanup(
            fake, ["--yes", "--no-spam", "--audit-log", self.audit]
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(fake.messages["INBOX"]), 4)
        self.assertEqual(fake.messages["[Gmail]/Spam"], {})
        for message in fake.messages["INBOX"].values():
            self.assertIn("\\Seen", message.flags)

    def test_no_unread_only_moves_spam(self):
        inbox = build_inbox(4, spam={1}, unread={2, 3})
        fake = self.make_fake(inbox)

        code, out, _ = run_cleanup(
            fake, ["--yes", "--no-unread", "--audit-log", self.audit]
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(fake.messages["[Gmail]/Spam"]), 1)
        # UID 3 and 4 were unread and must stay unread.
        self.assertNotIn("\\Seen", fake.messages["INBOX"][b"3"].flags)
        self.assertNotIn("\\Seen", fake.messages["INBOX"][b"4"].flags)

    def test_limit_applies_to_each_action(self):
        inbox = build_inbox(10, spam={0, 1, 2, 3}, unread=set(range(10)))
        fake = self.make_fake(inbox)

        code, out, _ = run_cleanup(
            fake,
            ["--yes", "--limit", "2", "--sample", "0", "--audit-log", self.audit],
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(fake.messages["[Gmail]/Spam"]), 2)
        # Two spam moved, then the next two unread messages are marked read.
        self.assertEqual(
            sum(1 for msg in fake.messages["INBOX"].values() if "\\Seen" in msg.flags),
            2,
        )

    def test_already_read_messages_are_untouched(self):
        inbox = build_inbox(3, unread=set())
        fake = self.make_fake(inbox)

        code, out, _ = run_cleanup(fake, ["--yes", "--sample", "0", "--audit-log", self.audit])

        self.assertEqual(code, 0)
        self.assertIn("Nothing to do", out)
        self.assertEqual(fake.logged_out, True)
        self.assertFalse(Path(self.audit).exists())

    def test_second_run_is_idempotent(self):
        inbox = build_inbox(5, spam={1, 3}, unread={0, 1, 2, 3})
        fake = self.make_fake(inbox)

        first_code, _, _ = run_cleanup(fake, ["--yes", "--sample", "0", "--audit-log", self.audit])
        inbox_after_first = set(fake.messages["INBOX"])
        spam_after_first = set(fake.messages["[Gmail]/Spam"])

        second_code, out, _ = run_cleanup(
            fake, ["--yes", "--sample", "0", "--audit-log", self.audit]
        )

        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertIn("Nothing to do", out)
        self.assertEqual(set(fake.messages["INBOX"]), inbox_after_first)
        self.assertEqual(set(fake.messages["[Gmail]/Spam"]), spam_after_first)

    def test_aborted_confirmation_changes_nothing(self):
        inbox = build_inbox(3, spam={1}, unread={0, 1, 2})
        fake = self.make_fake(inbox)

        with patch("builtins.input", return_value="n"):
            code, out, _ = run_cleanup(fake, ["--sample", "0", "--audit-log", self.audit])

        self.assertEqual(code, 1)
        self.assertIn("Aborted", out)
        self.assertEqual(len(fake.messages["INBOX"]), 3)
        self.assertEqual(fake.messages["[Gmail]/Spam"], {})

    def test_move_fallback_with_uidplus(self):
        inbox = build_inbox(4, spam={1}, unread={1})
        fake = self.make_fake(inbox, capabilities=b"IMAP4REV1 UIDPLUS")

        code, _, _ = run_cleanup(fake, ["--yes", "--sample", "0", "--audit-log", self.audit])

        self.assertEqual(code, 0)
        self.assertEqual(len(fake.messages["INBOX"]), 3)
        self.assertEqual(len(fake.messages["[Gmail]/Spam"]), 1)
        commands = [command for command, _ in fake.commands]
        self.assertIn("COPY", commands)
        self.assertNotIn("MOVE", commands)
        self.assertIn("EXPUNGE", commands)
        self.assertEqual(fake.expunge_calls, 0)

    def test_move_fallback_without_uidplus(self):
        inbox = build_inbox(4, spam={1}, unread={1})
        fake = self.make_fake(inbox, capabilities=b"IMAP4REV1")

        code, _, _ = run_cleanup(fake, ["--yes", "--sample", "0", "--audit-log", self.audit])

        self.assertEqual(code, 0)
        self.assertEqual(len(fake.messages["INBOX"]), 3)
        self.assertEqual(len(fake.messages["[Gmail]/Spam"]), 1)
        self.assertGreaterEqual(fake.expunge_calls, 1)

    def test_undo_restores_only_mark_read_rows(self):
        inbox = build_inbox(6, spam={1}, unread={0, 1, 2, 5})
        fake = self.make_fake(inbox)
        run_cleanup(fake, ["--yes", "--sample", "0", "--audit-log", self.audit])

        # Simulate the next day: everything left in INBOX is read.
        for message in fake.messages["INBOX"].values():
            message.flags.add("\\Seen")

        undo_fake = self.make_fake(fake.messages["INBOX"])
        with (
            patch.object(cm, "resolve_auth", return_value=DEFAULT_AUTH),
            patch.object(cm, "connect", return_value=undo_fake),
            patch.object(cm, "PAUSE", 0),
            redirect_stdout(io.StringIO()),
        ):
            code = cm.main(["--undo-read-audit", self.audit, "--yes"])

        self.assertEqual(code, 0)
        # Only UIDs recorded as plain mark_read (not spam/moved ones) become unread.
        self.assertEqual(undo_fake.messages["INBOX"][b"1"].flags, set())
        self.assertEqual(undo_fake.messages["INBOX"][b"3"].flags, set())
        self.assertEqual(undo_fake.messages["INBOX"][b"6"].flags, set())
        self.assertIn("\\Seen", undo_fake.messages["INBOX"][b"4"].flags)

    def test_undo_dry_run_changes_nothing(self):
        inbox = build_inbox(3, unread={0, 1})
        fake = self.make_fake(inbox)
        run_cleanup(fake, ["--yes", "--sample", "0", "--audit-log", self.audit])

        undo_fake = self.make_fake(fake.messages["INBOX"])
        with (
            patch.object(cm, "resolve_auth", return_value=DEFAULT_AUTH),
            patch.object(cm, "connect", return_value=undo_fake),
            redirect_stdout(io.StringIO()),
        ):
            code = cm.main(["--undo-read-audit", self.audit, "--dry-run"])

        self.assertEqual(code, 0)
        self.assertEqual(len(undo_fake.commands), 0)


if __name__ == "__main__":
    unittest.main()
