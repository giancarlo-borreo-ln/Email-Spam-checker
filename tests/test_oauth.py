"""Tests for gmail_oauth.py and the IMAP auth handshake, using fake Google libs."""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import cleanup_mail as cm
import gmail_oauth as go


class FakeRequest:
    pass


class FakeCredentials:
    instance = None
    loaded_from = None

    def __init__(self, token="tok", valid=True, expired=False, refresh_token="refresh"):
        self.token = token
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refreshed = False

    @classmethod
    def from_authorized_user_file(cls, path, scopes):
        cls.loaded_from = (path, tuple(scopes))
        return cls.instance

    def refresh(self, request):
        self.refreshed = True
        self.token = "refreshed-token"
        self.valid = True
        self.expired = False

    def to_json(self):
        return json.dumps({"token": self.token})


class FakeFlow:
    last = None
    next_credentials = None

    def __init__(self):
        self.path = None
        self.scopes = None
        self.kwargs = None
        self.used = False

    @classmethod
    def from_client_secrets_file(cls, path, scopes):
        flow = cls()
        flow.path = path
        flow.scopes = tuple(scopes)
        cls.last = flow
        return flow

    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        self.used = True
        return self.next_credentials


def fake_google_modules() -> dict[str, types.ModuleType]:
    google = types.ModuleType("google")
    google.__path__ = []
    auth = types.ModuleType("google.auth")
    auth.__path__ = []
    transport = types.ModuleType("google.auth.transport")
    transport.__path__ = []
    requests = types.ModuleType("google.auth.transport.requests")
    requests.Request = FakeRequest
    oauth2 = types.ModuleType("google.oauth2")
    oauth2.__path__ = []
    credentials = types.ModuleType("google.oauth2.credentials")
    credentials.Credentials = FakeCredentials
    oauthlib = types.ModuleType("google_auth_oauthlib")
    oauthlib.__path__ = []
    flow = types.ModuleType("google_auth_oauthlib.flow")
    flow.InstalledAppFlow = FakeFlow

    google.auth = auth
    google.oauth2 = oauth2
    auth.transport = transport
    transport.requests = requests
    oauth2.credentials = credentials
    oauthlib.flow = flow

    return {
        "google": google,
        "google.auth": auth,
        "google.auth.transport": transport,
        "google.auth.transport.requests": requests,
        "google.oauth2": oauth2,
        "google.oauth2.credentials": credentials,
        "google_auth_oauthlib": oauthlib,
        "google_auth_oauthlib.flow": flow,
    }


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.credentials = Path(self.tmp.name) / "credentials.json"
        self.credentials.write_text("{}", encoding="utf-8")
        self.token = Path(self.tmp.name) / "token.json"

        FakeCredentials.instance = None
        FakeCredentials.loaded_from = None
        FakeFlow.last = None
        FakeFlow.next_credentials = None

        self.modules = patch.dict(sys.modules, fake_google_modules())
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_returns_cached_valid_token(self):
        self.token.write_text("{}", encoding="utf-8")
        credentials = FakeCredentials(token="cached-token", valid=True)
        FakeCredentials.instance = credentials

        token = go.get_access_token(str(self.credentials), str(self.token))

        self.assertEqual(token, "cached-token")
        self.assertFalse(credentials.refreshed)
        self.assertIsNotNone(FakeCredentials.loaded_from)
        self.assertEqual(FakeCredentials.loaded_from[0], str(self.token))
        self.assertEqual(FakeCredentials.loaded_from[1], tuple(go.SCOPES))

    def test_refreshes_expired_token(self):
        self.token.write_text("{}", encoding="utf-8")
        credentials = FakeCredentials(
            token="old-token", valid=False, expired=True, refresh_token="refresh-me"
        )
        FakeCredentials.instance = credentials

        token = go.get_access_token(str(self.credentials), str(self.token))

        self.assertTrue(credentials.refreshed)
        self.assertEqual(token, "refreshed-token")
        self.assertIn("refreshed-token", self.token.read_text(encoding="utf-8"))

    def test_runs_browser_flow_when_token_cannot_be_used(self):
        credentials = FakeCredentials(
            token="stale", valid=False, expired=False, refresh_token=None
        )
        FakeCredentials.instance = credentials
        FakeFlow.next_credentials = FakeCredentials(token="browser-token", valid=True)

        token = go.get_access_token(str(self.credentials), str(self.token))

        self.assertEqual(token, "browser-token")
        flow = FakeFlow.last
        self.assertIsNotNone(flow)
        assert flow is not None
        self.assertTrue(flow.used)
        self.assertEqual(flow.path, str(self.credentials))
        self.assertEqual(flow.kwargs["port"], 0)
        self.assertTrue(flow.kwargs["open_browser"])
        self.assertEqual(flow.kwargs["access_type"], "offline")
        self.assertIn("browser-token", self.token.read_text(encoding="utf-8"))

    def test_force_ignores_cached_token(self):
        self.token.write_text("{}", encoding="utf-8")
        FakeCredentials.instance = FakeCredentials(token="cached", valid=True)
        FakeFlow.next_credentials = FakeCredentials(token="fresh-token", valid=True)

        token = go.get_access_token(str(self.credentials), str(self.token), force=True)

        self.assertEqual(token, "fresh-token")
        self.assertIsNone(FakeCredentials.loaded_from)
        assert FakeFlow.last is not None
        self.assertTrue(FakeFlow.last.used)

    def test_missing_credentials_file_raises_with_setup_help(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            go.get_access_token("/missing/credentials.json", str(self.token))
        self.assertIn("console.cloud.google.com", str(ctx.exception))

    def test_missing_package_gives_install_hint(self):
        with patch.dict(sys.modules, {"google.auth.transport.requests": None}):
            with self.assertRaises(RuntimeError) as ctx:
                go.get_access_token(str(self.credentials), str(self.token))
        self.assertIn("google-auth-oauthlib", str(ctx.exception))


class FakeSocketIMAP:
    def __init__(self, *args, **kwargs):
        self.auth = None
        self.login_args = None
        self.logged_out = False

    def authenticate(self, mechanism, callback):
        self.auth = (mechanism, callback(None))
        return "OK", [b""]

    def login(self, user, password):
        self.login_args = (user, password)
        return "OK", [b""]

    def logout(self):
        self.logged_out = True


class ConnectTests(unittest.TestCase):
    def test_oauth_builds_xoauth2_string(self):
        fake = FakeSocketIMAP()
        auth = cm.Auth(user="me@example.test", mode="oauth")

        with (
            patch.object(cm.imaplib, "IMAP4_SSL", return_value=fake),
            patch("gmail_oauth.get_access_token", return_value="tok-123"),
        ):
            conn = cm.connect(auth, "imap.gmail.com", 993)

        self.assertIs(conn, fake)
        self.assertEqual(
            fake.auth,
            ("XOAUTH2", b"user=me@example.test\x01auth=Bearer tok-123\x01\x01"),
        )

    def test_app_password_uses_login(self):
        fake = FakeSocketIMAP()
        auth = cm.Auth(user="me@example.test", mode="app-password", password="secret")

        with patch.object(cm.imaplib, "IMAP4_SSL", return_value=fake):
            conn = cm.connect(auth, "imap.gmail.com", 993)

        self.assertIs(conn, fake)
        self.assertEqual(fake.login_args, ("me@example.test", "secret"))


if __name__ == "__main__":
    unittest.main()
