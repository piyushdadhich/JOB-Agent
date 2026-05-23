"""One-time OAuth2 consent flow for read-only Gmail access.

Usage:
  python scripts/gmail_auth.py --profile default

Prerequisites: data/{profile}/gmail_credentials.json must exist
(downloaded from Google Cloud Console; see Phase 8 setup notes).

Scope: gmail.readonly only — the agent reads recruiter / newsletter
emails to extract job postings. It never sends or modifies mail.

What it does:
  1. Opens a browser for OAuth consent.
  2. Saves the refresh token to data/{profile}/gmail_token.json
     (gitignored).
  3. Verifies the token works by listing the 5 most recent
     message IDs.

Re-run if the token expires or is revoked. Refresh tokens issued
by Google Cloud projects in 'Testing' status expire after 7 days
— publish the OAuth screen to keep tokens valid longer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _credentials_path(profile_id: str) -> Path:
    return PROJECT_ROOT / "data" / profile_id / "gmail_credentials.json"


def _token_path(profile_id: str) -> Path:
    return PROJECT_ROOT / "data" / profile_id / "gmail_token.json"


def authorize(profile_id: str, scopes: list[str]) -> Credentials:
    cred_path = _credentials_path(profile_id)
    token_path = _token_path(profile_id)
    if not cred_path.exists():
        raise FileNotFoundError(
            f"OAuth credentials not found at {cred_path}. "
            f"Download from Google Cloud Console and save there."
        )

    creds: Optional[Credentials] = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(
            str(token_path), scopes,
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(cred_path), scopes,
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"Saved token to {token_path}")
    return creds


def list_recent_messages(creds: Credentials, n: int = 5) -> list[str]:
    service = build("gmail", "v1", credentials=creds)
    resp = service.users().messages().list(
        userId="me", maxResults=n,
    ).execute()
    return [m["id"] for m in resp.get("messages", [])]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    args = parser.parse_args(argv)

    print("Requesting Gmail scope: readonly")
    creds = authorize(args.profile, READONLY_SCOPES)
    ids = list_recent_messages(creds, n=5)
    print(f"\nGmail auth ok (readonly). Most recent {len(ids)} message IDs:")
    for i in ids:
        print(f"  {i}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
