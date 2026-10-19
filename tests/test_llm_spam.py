"""Tests for llm_spam_scan.py using a fake Ollama HTTP API."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import cleanup_mail as cm
import llm_spam_scan as llm
from tests.fake_imap import STANDARD_FOLDERS, FakeIMAP, Message

AUTH = cm.Auth(user="me@example.test", mode="app-password", password="secret")
MODEL = "test:1b"


def mail_header(sender: str, subject: str, message_id: str) -> bytes:
    return (
        f"From: {sender}\r\n"
        f"To: me@example.test\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Mon, 19 Oct 2026 10:00:00 +0000\r\n"
        f"Message-ID: <{message_id}>\r\n\r\n"
    ).encode()


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOllama:
    def __init__(self, verdict=None):
        self.chat_calls: list[dict] = []
        self.verdict = verdict or self.default_verdict

    @staticmethod
    def default_verdict(email_text: str):
        if "spam@bad.test" in email_text:
            return "spam", 0.97, "unsolicited advertisement"
        if "scam@bad.test" in email_text:
            return "spam", 0.60, "possible scam, low confidence"
        return "ham", 0.98, "looks legitimate"

    def __call__(self, request, timeout=None):
        if request.full_url.endswith("/api/tags"):
            return FakeResponse({"models": [{"name": MODEL}]})
        payload = json.loads(request.data)
        self.chat_calls.append(payload)
        email_text = payload["messages"][1]["content"]
        label, confidence, reason = self.verdict(email_text)
        return FakeResponse({
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "label": label,
                    "confidence": confidence,
                    "reason": reason,
                }),
            },
            "prompt_eval_count": 120,
            "eval_count": 12,
            "done_reason": "stop",
        })


def build_fake(verdict=None) -> tuple[FakeIMAP, FakeOllama]:
    inbox = {
        b"1": Message(mail_header("spam@bad.test", "Buy cheap pills", "m1"), set()),
        b"2": Message(mail_header("friend@good.test", "Dinner on Saturday", "m2"), set()),
        b"3": Message(mail_header("scam@bad.test", "Maybe crypto", "m3"), set()),
        b"4": Message(mail_header("boss@work.test", "Quarterly report", "m4"), set()),
    }
    fake = FakeIMAP(
        STANDARD_FOLDERS,
        {
            "INBOX": inbox,
            "[Gmail]/Spam": {},
            "[Gmail]/Sent Mail": {},
            "[Gmail]/All Mail": {},
            "[Gmail]/Trash": {},
            "[Gmail]/Drafts": {},
        },
    )
    return fake, FakeOllama(verdict)


def run_llm(fake, ollama, argv, cwd: str):
    out, err = io.StringIO(), io.StringIO()
    with (
        patch.object(cm, "resolve_auth", return_value=AUTH),
        patch.object(cm, "connect", return_value=fake),
        patch.object(cm, "PAUSE", 0),
        patch.object(llm.urllib.request, "urlopen", side_effect=ollama),
        contextlib.chdir(cwd),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        code = llm.main(argv)
    return code, out.getvalue(), err.getvalue()


class ClientTests(unittest.TestCase):
    def test_each_email_is_a_fresh_two_message_request(self):
        chat: FakeOllama = FakeOllama()
        client = llm.OllamaClient(model=MODEL, num_ctx=2048)
        with patch.object(llm.urllib.request, "urlopen", side_effect=chat):
            client.classify(llm.EmailMeta(from_="a@x.test", subject="First email"))
            client.classify(llm.EmailMeta(from_="b@y.test", subject="Second email"))

        self.assertEqual(len(chat.chat_calls), 2)
        for payload, subject, forbidden in [
            (chat.chat_calls[0], "First email", "Second email"),
            (chat.chat_calls[1], "Second email", "First email"),
        ]:
            messages = payload["messages"]
            self.assertEqual([m["role"] for m in messages], ["system", "user"])
            self.assertIn(subject, messages[1]["content"])
            self.assertNotIn(forbidden, messages[1]["content"])
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["format"], "json")
            self.assertFalse(payload["think"])
            self.assertEqual(payload["options"]["num_ctx"], 2048)

    def test_context_pressure_is_detected(self):
        response = FakeResponse({
            "message": {"content": '{"label":"spam","confidence":0.99,"reason":"x"}'},
            "prompt_eval_count": 2000,
            "eval_count": 10,
            "done_reason": "stop",
        })
        client = llm.OllamaClient(model=MODEL, num_ctx=2048)
        with patch.object(llm.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(llm.ContextPressure):
                client.classify(llm.EmailMeta(from_="a@x.test", subject="x"))

    def test_truncated_generation_is_an_error(self):
        response = FakeResponse({
            "message": {"content": '{"label":"spam","confidence":0.99,"reason":"x"}'},
            "prompt_eval_count": 100,
            "eval_count": 64,
            "done_reason": "length",
        })
        client = llm.OllamaClient(model=MODEL, num_ctx=2048)
        with patch.object(llm.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(llm.OllamaError):
                client.classify(llm.EmailMeta(from_="a@x.test", subject="x"))

    def test_parses_fenced_json(self):
        self.assertEqual(
            llm.parse_json_object('```json\n{"label":"ham","confidence":0.9}\n```'),
            {"label": "ham", "confidence": 0.9},
        )

    def test_extracts_json_from_chatter(self):
        self.assertEqual(
            llm.parse_json_object('Verdict: {"label":"spam"} done'),
            {"label": "spam"},
        )

    def test_normalize_label(self):
        self.assertEqual(llm.normalize_label("SPAM"), "spam")
        self.assertEqual(llm.normalize_label("phishing"), "spam")
        self.assertEqual(llm.normalize_label("not spam"), "ham")
        with self.assertRaises(ValueError):
            llm.normalize_label("maybe")

    def test_allowlist_and_cache_key(self):
        rules = ["good.test", "boss@work.test"]
        self.assertTrue(llm.is_allowlisted(
            llm.EmailMeta(from_="Friend <a@good.test>"), rules))
        self.assertFalse(llm.is_allowlisted(
            llm.EmailMeta(from_="spam@bad.test"), rules))
        self.assertEqual(
            llm.cache_key(llm.EmailMeta(message_id="<abc@x>"), "INBOX"), "<abc@x>")
        self.assertEqual(
            llm.cache_key(llm.EmailMeta(uid="7", subject="Hi"), "INBOX"),
            "INBOX:7:Hi",
        )


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.verdicts = str(Path(self.dir) / "verdicts.csv")
        self.cache = str(Path(self.dir) / "cache.json")

    def base_args(self):
        return [
            "--auth", "app-password",
            "--model", MODEL,
            "--verdicts", self.verdicts,
            "--cache", self.cache,
            "--threshold", "0.90",
        ]

    def test_apply_moves_only_high_confidence_spam(self):
        fake, ollama = build_fake()
        code, out, err = run_llm(
            fake, ollama, self.base_args() + ["--apply", "--yes"], self.dir)

        self.assertEqual(code, 0, err)
        self.assertEqual(set(fake.messages["INBOX"]), {b"2", b"3", b"4"})
        self.assertEqual(len(fake.messages["[Gmail]/Spam"]), 1)
        self.assertIn("Moved 1 message", out)

        with open(self.verdicts, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 4)
        labels = {row["uid"]: row["label"] for row in rows}
        self.assertEqual(labels["1"], "spam")
        self.assertEqual(labels["3"], "spam")  # low confidence, not moved
        self.assertEqual(labels["2"], "ham")
        self.assertEqual(labels["4"], "ham")

        audit = Path(self.dir) / "llm-spam-audit.csv"
        self.assertTrue(audit.exists())
        self.assertIn("llm_move_to_spam", audit.read_text())

    def test_scan_only_changes_nothing(self):
        fake, ollama = build_fake()
        code, out, _ = run_llm(fake, ollama, self.base_args(), self.dir)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.messages["INBOX"]), 4)
        self.assertEqual(fake.messages["[Gmail]/Spam"], {})
        self.assertIn("Scan only", out)

    def test_cache_prevents_reclassification(self):
        fake, ollama = build_fake()
        run_llm(fake, ollama, self.base_args(), self.dir)
        first_calls = len(ollama.chat_calls)
        self.assertEqual(first_calls, 4)

        run_llm(fake, ollama, self.base_args(), self.dir)
        self.assertEqual(len(ollama.chat_calls), first_calls)

    def test_allowlist_forces_ham(self):
        fake, ollama = build_fake(
            verdict=lambda text: ("spam", 0.99, "aggressive but allowed"))
        allow = Path(self.dir) / "allow.txt"
        allow.write_text("friend@good.test\n", encoding="utf-8")

        code, _, _ = run_llm(
            fake,
            ollama,
            self.base_args() + ["--apply", "--yes", "--allow", str(allow)],
            self.dir,
        )
        self.assertEqual(code, 0)
        # uid1 and uid3 are still moved; uid2 was forced to ham by allowlist.
        self.assertIn(b"2", fake.messages["INBOX"])

    def test_malformed_model_output_is_recorded_as_error(self):
        class BadOllama(FakeOllama):
            def __call__(self, request, timeout=None):
                if request.full_url.endswith("/api/tags"):
                    return FakeResponse({"models": [{"name": MODEL}]})
                return FakeResponse({
                    "message": {"content": "I think this is spam, probably."},
                    "prompt_eval_count": 50,
                    "eval_count": 10,
                    "done_reason": "stop",
                })

        fake, _ = build_fake()
        code, out, _ = run_llm(
            fake, BadOllama(), self.base_args() + ["--apply", "--yes"], self.dir)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.messages["INBOX"]), 4)
        self.assertIn("errors: 4", out)


if __name__ == "__main__":
    unittest.main()
