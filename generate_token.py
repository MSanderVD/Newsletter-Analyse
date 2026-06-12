"""
Einmalig lokal ausführen, um das Gmail OAuth-Token zu generieren.
Lege credentials.json vorher in diesen Ordner.

  pip install google-auth-oauthlib google-api-python-client
  python generate_token.py

→ Gibt den Inhalt für GMAIL_TOKEN_JSON aus (direkt in GitHub Secrets eintragen).
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

flow  = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(port=0)

token_data = {
    "token":         creds.token,
    "refresh_token": creds.refresh_token,
    "token_uri":     creds.token_uri,
    "client_id":     creds.client_id,
    "client_secret": creds.client_secret,
    "scopes":        list(creds.scopes),
}

print("\n✅ Fertig! Diesen Wert als GitHub Secret GMAIL_TOKEN_JSON eintragen:\n")
print(json.dumps(token_data))
