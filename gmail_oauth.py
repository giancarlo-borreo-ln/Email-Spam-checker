"""Google OAuth 2.0 helper for cleanup_mail.py.

Uses the installed-app (loopback) flow: the browser opens, you pick your
Google account, and the resulting token is cached in token.json.

Requires: pip install -r requirements.txt
"""

from __future__ import annotations

from pathlib import Path

SCOPES = ["https://mail.google.com/"]

MISSING_PACKAGES = (
    "Google OAuth needs the google-auth-oauthlib package:\n"
    "    pip install -r requirements.txt\n"
    "or:  pip install google-auth-oauthlib"
)


def get_access_token(
    credentials_path: str,
    token_path: str,
    force: bool = False,
) -> str:
    """Return a valid Google access token, opening the browser if needed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(MISSING_PACKAGES) from exc

    token_file = Path(token_path).expanduser()
    credentials_file = Path(credentials_path).expanduser()

    creds = None
    if token_file.exists() and not force:
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except (OSError, ValueError):
            creds = None

    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            print("Refreshing Google access token...", flush=True)
            creds.refresh(Request())
        else:
            creds = None

    if not creds:
        if not credentials_file.exists():
            raise FileNotFoundError(
                f"Google OAuth client file not found: {credentials_file}\n\n"
                "Create one once in Google Cloud Console:\n"
                "  1. Create/select a project at https://console.cloud.google.com/\n"
                "  2. APIs & Services > Library > enable 'Gmail API'\n"
                "  3. OAuth consent screen > External > add yourself as a test user\n"
                "  4. Credentials > Create credentials > OAuth client ID > Desktop app\n"
                "  5. Download the JSON and save it as credentials.json here\n"
            )

        print("Opening your browser to sign in to Google...", flush=True)
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            access_type="offline",
            prompt="consent",
        )

    if not creds.token:
        raise RuntimeError("Google did not return an access token. Try --reauth.")

    # Persist the token (and any refreshed value) for the next run.
    try:
        token_file.write_text(creds.to_json(), encoding="utf-8")
    except OSError:
        pass

    return creds.token
