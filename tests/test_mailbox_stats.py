"""Tests for mailbox_stats.py."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import cleanup_mail as cm
import mailbox_stats as ms
from tests.fake_imap import STANDARD_FOLDERS, FakeIMAP, Message, make_header

AUTH = cm.Auth(user="me@example.test", mode="app-password", password="secret")


def sent_header(index: int, subject: str, date: str) -> bytes:
    return (
        f"From: me@example.test\r\n"
        f"To: recipient{index}@example.test\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {date}\r\n"
        f"Message-ID: <sent-{index}@example.test>\r\n\r\n"
    ).encode()


def received_header(index: int, subject: str, date: str) -> bytes:
    return (
        f"From: friend{index}@example.test\r\n"
        f"To: me@example.test\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {date}\r\n"
        f"Message-ID: <recv-{index}@example.test>\r\n\r\n"
    ).encode()


def build_fake(sent_empty: bool = False) -> FakeIMAP:
    inbox = {
        b"1": Message(make_header(1), set()),
        b"2": Message(make_header(2), set()),
        b"3": Message(make_header(3), {"\\Seen"}),
    }

    sent = {} if sent_empty else {
        # UID order is deliberately not Date order. UID 5 has the oldest Date
        # header but a much newer INTERNALDATE (imported/migrated message).
        b"1": Message(sent_header(1, "Second message", "Mon, 01 May 2010 10:00:00 +0000"),
                      {"\\Seen"}, "01-May-2010 10:00:00 +0000"),
        b"2": Message(sent_header(2, "Later but lower UID", "Tue, 15 Jan 2008 09:30:00 +0000"),
                      {"\\Seen"}, "15-Jan-2008 09:30:00 +0000"),
        b"3": Message(sent_header(3, "Third message", "Wed, 20 Jun 2012 12:00:00 +0000"),
                      {"\\Seen"}, "20-Jun-2012 12:00:00 +0000"),
        b"4": Message(sent_header(4, "Imported old message", "Thu, 03 Mar 2009 08:00:00 +0000"),
                      {"\\Seen"}, "03-Mar-2009 08:00:00 +0000"),
        b"5": Message(sent_header(5, "Old but imported later", "Wed, 01 Feb 2006 08:00:00 +0000"),
                      {"\\Seen"}, "01-Jan-2015 00:00:00 +0000"),
    }

    spam = {
        str(index).encode(): Message(make_header(index), set())
        for index in range(1, 4)
    }
    spam.update({
        str(index).encode(): Message(make_header(index), {"\\Seen"})
        for index in range(4, 6)
    })

    # All Mail is the authoritative "everything except Spam/Trash" view.
    all_mail = {
        b"1": Message(sent_header(90, "Sent copy", "Mon, 01 Jan 2007 10:00:00 +0000"),
                      {"\\Seen"}, "01-Jan-2007 10:00:00 +0000"),
        b"2": Message(received_header(2, "First received message", "Fri, 01 Jun 2007 10:00:00 +0000"),
                      {"\\Seen"}, "01-Jun-2007 10:00:00 +0000"),
        b"3": Message(sent_header(91, "Another sent copy", "Tue, 01 Jan 2008 10:00:00 +0000"),
                      {"\\Seen"}, "01-Jan-2008 10:00:00 +0000"),
    }
    for index in range(4, 13):
        all_mail[str(index).encode()] = Message(
            received_header(index, f"Received {index}", f"1-Jan-{2007 + index} 10:00:00 +0000"),
            {"\\Seen"},
            f"1-Jan-{2007 + index} 10:00:00 +0000",
        )

    return FakeIMAP(
        STANDARD_FOLDERS,
        {
            "INBOX": inbox,
            "[Gmail]/Sent Mail": sent,
            "[Gmail]/Drafts": {b"1": Message(make_header(200), {"\\Seen"})},
            "[Gmail]/Spam": spam,
            "[Gmail]/Trash": {
                b"1": Message(make_header(300), {"\\Seen"}),
                b"2": Message(make_header(301), {"\\Seen"}),
            },
            "[Gmail]/All Mail": all_mail,
        },
    )


def run_stats(fake: FakeIMAP, argv: list[str] | None = None):
    out, err = io.StringIO(), io.StringIO()
    with (
        patch.object(cm, "resolve_auth", return_value=AUTH),
        patch.object(cm, "connect", return_value=fake),
        patch.object(cm, "PAUSE", 0),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        code = ms.main(argv or [])
    return code, out.getvalue(), err.getvalue()


class StatsTests(unittest.TestCase):
    def test_json_counts_every_folder(self):
        fake = build_fake()
        code, out, err = run_stats(fake, ["--json"])

        self.assertEqual(code, 0, err)
        data = json.loads(out)
        rows = {row["label"]: row for row in data["folders"]}

        self.assertEqual(rows["INBOX"]["messages"], 3)
        self.assertEqual(rows["INBOX"]["unread"], 2)
        self.assertEqual(rows["Sent Mail"]["messages"], 5)
        self.assertEqual(rows["Drafts"]["messages"], 1)
        self.assertEqual(rows["Spam"]["messages"], 5)
        self.assertEqual(rows["Spam"]["unread"], 3)
        self.assertEqual(rows["Trash"]["messages"], 2)
        self.assertEqual(rows["All Mail"]["messages"], 12)

    def test_first_sent_uses_date_header_not_internal_date(self):
        fake = build_fake()
        code, out, _ = run_stats(fake, ["--json"])

        self.assertEqual(code, 0)
        first = json.loads(out)["first_sent"]
        self.assertEqual(first["uid"], "5")
        self.assertEqual(first["subject"], "Old but imported later")
        self.assertTrue(first["date"].startswith("2006-02-01"))
        self.assertTrue(first["internal_date"].startswith("2015-01-01"))
        self.assertEqual(first["date_source"], "Date header")

    def test_first_received_skips_messages_from_you(self):
        fake = build_fake()
        code, out, _ = run_stats(fake, ["--json"])

        self.assertEqual(code, 0)
        first = json.loads(out)["first_received"]
        self.assertEqual(first["uid"], "2")
        self.assertEqual(first["subject"], "First received message")
        self.assertTrue(first["date"].startswith("2007-06-01"))
        self.assertIn("friend2@example.test", first["from_"])

    def test_oldest_lists_are_sorted(self):
        fake = build_fake()
        code, out, _ = run_stats(fake, ["--json", "--oldest", "3"])

        self.assertEqual(code, 0)
        data = json.loads(out)
        sent_dates = [item["date"] for item in data["oldest_sent"]]
        received_dates = [item["date"] for item in data["oldest_received"]]
        self.assertEqual(sent_dates, sorted(sent_dates))
        self.assertEqual(received_dates, sorted(received_dates))
        self.assertEqual(len(data["oldest_sent"]), 3)
        self.assertEqual(len(data["oldest_received"]), 3)

    def test_all_folder_access_is_read_only(self):
        fake = build_fake()
        run_stats(fake, ["--json"])
        self.assertTrue(fake.readonly_history)
        self.assertTrue(all(readonly for _, readonly in fake.readonly_history))

    def test_no_oldest_skips_scans(self):
        fake = build_fake()
        code, out, _ = run_stats(fake, ["--json", "--no-first-sent"])

        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIsNone(data["first_sent"])
        self.assertIsNone(data["first_received"])
        internal_date_fetches = [
            (command, args)
            for command, args in fake.commands
            if command == "FETCH" and any("INTERNALDATE" in str(arg) for arg in args)
        ]
        self.assertEqual(internal_date_fetches, [])

    def test_empty_sent_folder(self):
        fake = build_fake(sent_empty=True)
        code, out, _ = run_stats(fake, ["--json"])

        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIsNone(data["first_sent"])
        self.assertEqual(data["oldest_sent"], [])
        rows = {row["label"]: row for row in data["folders"]}
        self.assertEqual(rows["Sent Mail"]["messages"], 0)

    def test_human_output_contains_both_directions(self):
        fake = build_fake()
        code, out, _ = run_stats(fake)

        self.assertEqual(code, 0)
        self.assertIn("Mailbox statistics for me@example.test", out)
        self.assertIn("Oldest messages you sent", out)
        self.assertIn("Oldest messages you received", out)
        self.assertIn("Old but imported later", out)
        self.assertIn("First received message", out)
        self.assertIn("2006-02-01", out)

    def test_parse_internal_date(self):
        self.assertEqual(
            ms.parse_internal_date("15-Jan-2008 09:30:00 +0000").year,
            2008,
        )
        self.assertIsNone(ms.parse_internal_date("not a date"))

    def test_parse_date_header(self):
        parsed = ms.parse_date_header("Tue, 15 Jan 2008 09:30:00 +0000")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.year, 2008)
        self.assertIsNone(ms.parse_date_header("not a date"))
        self.assertIsNone(ms.parse_date_header(None))

    def test_find_folder_prefers_flag_then_name(self):
        folders = [cm.parse_folder_entry(line) for line in STANDARD_FOLDERS]
        folders = [folder for folder in folders if folder]
        sent = ms.find_folder(folders, ("\\SENT",), ("Nope",))
        self.assertIsNotNone(sent)
        assert sent is not None
        self.assertEqual(sent.raw, "[Gmail]/Sent Mail")
        self.assertIsNone(ms.find_folder(folders, names=("Nope",)))


if __name__ == "__main__":
    unittest.main()
