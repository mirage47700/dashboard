#!/usr/bin/env python3
"""
Authenticate Google Calendar via OAuth2 console flow.
Run on the server: python3 auth_google.py
Then paste the URL in your browser, authorize, copy the code back.
"""
import json, os, sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    print("ERREUR: GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET doivent etre dans .env")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
TOKEN_PATH = Path(__file__).parent / "data" / "google_token.json"
TOKEN_PATH.parent.mkdir(exist_ok=True)

from google_auth_oauthlib.flow import InstalledAppFlow

client_config = {
    "installed": {
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
creds = flow.run_console()

token_data = {
    "token":         creds.token,
    "refresh_token": creds.refresh_token,
    "token_uri":     creds.token_uri,
    "client_id":     creds.client_id,
    "client_secret": creds.client_secret,
    "scopes":        list(creds.scopes) if creds.scopes else SCOPES,
    "expiry":        creds.expiry.isoformat() if creds.expiry else None,
}

TOKEN_PATH.write_text(json.dumps(token_data, indent=2))
print(f"\nToken sauvegarde dans {TOKEN_PATH}")
print("Google Calendar connecte !")
