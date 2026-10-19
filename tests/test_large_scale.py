"""End-to-end test on a 70,000-message mailbox.

The point is to prove the batching, spam detection, audit log and neither
losing nor duplicating messages at a realistic scale.
"""

from __future__ import annotations

import collections
import csv
import os
import tempfile
import time
import unittest
from pathlib import Path

import cleanup_mail as cm
from tests.fake_imap import STANDARD_FOLDERS, FakeIMAP, Message, make_header
from tests.test_cleanup import run_cleanup

# Scale the simulation with CLEANUP_TEST_SIZE (default 70,000).
INBOX_SIZE = int(os.environ.get("CLEANUP_TEST_SIZE", "70000"))
SPAM_SIZE = INBOX_SIZE * 2 // 7
UNREAD_SIZE = INBOX_SIZE * 6 // 7
DECOY_SIZE = min(100, max(1, INBOX_SIZE // 100))
EXISTING_SPAM = 5


def build_large_mailbox() -> FakeIMAP:
    inbox: dict[bytes, Message] = {}
    for index in range(INBOX_SIZE):
        if index < SPAM_SIZE:
            if index % 2 == 0:
                header = make_header(index, spam_flag="YES")
            else:
                header = make_header(
                    index,
                    spam_status="Yes, score=9.0 required=5.0 tests=FAKE",
                )
        elif index < SPAM_SIZE + DECOY_SIZE:
            # Looks suspicious in a naive keyword scan, but is explicitly not spam.
            header = make_header(
                index,
                spam_flag="NO",
                spam_status="No, score=-1.0 required=5.0",
            )
        else:
            header = make_header(index)

        flags = set() if index < UNREAD_SIZE else {"\\Seen"}
        inbox[str(index + 1).encode()] = Message(header=header, flags=flags)

    spam = {
        str(1_000_000 + index).encode(): Message(
            header=make_header(1_000_000 + index, spam_flag="YES"),
            flags={"\\Seen"},
        )
        for index in range(EXISTING_SPAM)
    }

    return FakeIMAP(
        STANDARD_FOLDERS,
        {
            "INBOX": inbox,
            "[Gmail]/Spam": spam,
            "[Gmail]/Sent Mail": {},
            "[Gmail]/All Mail": {},
            "[Gmail]/Trash": {},
            "[Gmail]/Drafts": {},
        },
    )


class LargeScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = build_large_mailbox()
        cls.before_headers = collections.Counter(
            message.header
            for mailbox in cls.fake.messages.values()
            for message in mailbox.values()
        )

        cls.tmp = tempfile.TemporaryDirectory()
        cls.audit = str(Path(cls.tmp.name) / "audit-70k.csv")

        started = time.perf_counter()
        cls.code, cls.stdout, cls.stderr = run_cleanup(
            cls.fake,
            [
                "--yes",
                "--sample",
                "0",
                "--audit-log",
                cls.audit,
            ],
        )
        cls.elapsed = time.perf_counter() - started

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exit_code_and_runtime(self):
        self.assertEqual(self.code, 0, self.stderr)
        self.assertLess(self.elapsed, 300, f"cleanup took {self.elapsed:.1f}s")

    def test_plan_counts(self):
        self.assertIn(f"Spam-header matches: {SPAM_SIZE}", self.stdout)
        self.assertIn(f"Unread messages: {UNREAD_SIZE}", self.stdout)

    def test_inbox_keeps_only_ham(self):
        inbox = self.fake.messages["INBOX"]
        self.assertEqual(len(inbox), INBOX_SIZE - SPAM_SIZE)
        self.assertEqual(
            sum(
                1
                for message in inbox.values()
                if b"X-Spam-Flag: YES" in message.header
                or b"X-Spam-Status: Yes" in message.header
            ),
            0,
        )

    def test_spam_folder_received_every_spam_message(self):
        spam = self.fake.messages["[Gmail]/Spam"]
        self.assertEqual(len(spam), SPAM_SIZE + EXISTING_SPAM)
        self.assertEqual(
            sum(
                1
                for message in spam.values()
                if b"X-Spam-Flag: YES" in message.header
                or b"X-Spam-Status: Yes" in message.header
            ),
            SPAM_SIZE + EXISTING_SPAM,
        )

    def test_nothing_was_lost_or_duplicated(self):
        after_headers = collections.Counter(
            message.header
            for mailbox in self.fake.messages.values()
            for message in mailbox.values()
        )
        self.assertEqual(after_headers, self.before_headers)

    def test_everything_left_in_inbox_is_read(self):
        for message in self.fake.messages["INBOX"].values():
            self.assertIn("\\Seen", message.flags)

    def test_moved_messages_were_marked_read(self):
        for message in self.fake.messages["[Gmail]/Spam"].values():
            self.assertIn("\\Seen", message.flags)

    def test_audit_log_is_complete(self):
        actions = collections.Counter()
        action_keys: set[tuple[str, str, str]] = set()
        row_count = 0
        check_unique = INBOX_SIZE <= 200_000
        with open(self.audit, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                row_count += 1
                actions[row["action"]] += 1
                if check_unique:
                    action_keys.add((row["action"], row["folder"], row["uid"]))

        self.assertEqual(actions["move_to_spam"], SPAM_SIZE)
        self.assertEqual(actions["mark_read_for_spam"], SPAM_SIZE)
        self.assertEqual(actions["mark_read"], UNREAD_SIZE - SPAM_SIZE)
        self.assertEqual(row_count, SPAM_SIZE * 2 + (UNREAD_SIZE - SPAM_SIZE))
        if check_unique:
            self.assertEqual(row_count, len(action_keys))

    def test_command_batches_stay_small(self):
        self.assertTrue(self.fake.uid_set_sizes)
        max_store = max(
            size for command, size in self.fake.uid_set_sizes if command == "STORE"
        )
        max_fetch = max(
            size for command, size in self.fake.uid_set_sizes if command == "FETCH"
        )
        self.assertLessEqual(max_store, cm.COMMAND_CHUNK)
        self.assertLessEqual(max_fetch, cm.FETCH_CHUNK)

    def test_used_move_extension(self):
        commands = [command for command, _ in self.fake.commands]
        self.assertIn("MOVE", commands)
        self.assertNotIn("COPY", commands)

    def test_summary_output(self):
        self.assertIn(f"moved {SPAM_SIZE}", self.stdout)
        self.assertIn(f"Marked {UNREAD_SIZE}", self.stdout)


if __name__ == "__main__":
    unittest.main()
