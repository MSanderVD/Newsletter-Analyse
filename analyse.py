"""
Newsletter-Analyse – kostenlose Variante
Läuft als GitHub Actions Workflow (täglich / wöchentlich / monatlich).
Gmail → Gemini AI (kostenlos) → Report per Email + OneDrive-Upload
"""

import os
import sys
import json
import datetime
import base64
import re
import argparse
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

def get_gmail_service():
    creds_json  = os.environ["GMAIL_CREDENTIALS_JSON"]
    token_json  = os.environ["GMAIL_TOKEN_JSON"]

    creds_data  = json.loads(token_json)
    client_info = json.loads(creds_json)["installed"]

    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_info["client_id"],
        client_secret=client_info["client_secret"],
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
        ],
    )
    return build("gmail", "v1", credentials=creds)


def fetch_emails(service, days_back: int) -> list[dict]:
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=days_back)).strftime("%Y/%m/%d")
    result = service.users().messages().list(
        userId="me", q=f"after:{since}", maxResults=500
    ).execute()
    messages = result.get("messages", [])

    emails = []
    for ref in messages:
        msg = service.users().messages().get(
            userId="me", id=ref["id"], format="full"
        ).execute()

        subject = sender = ""
        for h in msg["payload"].get("headers", []):
            if h["name"] == "Subject": subject = h["value"]
            if h["name"] == "From":    sender  = h["value"]

        body = _extract_body(msg["payload"])
        emails.append({"subject": subject, "sender": sender, "body": body[:3000]})

    logger.info(f"{len(emails)} Emails der letzten {days_back} Tage gefunden.")
    return emails


def _extract_body(payload: dict) -> str:
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain":
                data = part["body"].get("data", "")
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
        return _extract_body(payload["parts"][0])
    data = payload["body"].get("data", "")
    if data:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    return ""


def send_email(service, to: str, subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = "vdnewsletteranalyse@gmail.com"
    msg["To"]      = to
    msg.attach(MIMEText(html_body, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    logger.info(f"Email gesendet an {to}")


# ---------------------------------------------------------------------------
# OpenRouter AI (kostenlos mit GitHub-Login)
# ---------------------------------------------------------------------------

def analyse_with_gemini(emails: list[dict], period_label: str) -> str:
    api_key = os.environ["OPENROUTER_API_KEY"]

    email_texts = []
    for i, e in enumerate(emails[:80], 1):
        email_texts.append(
            f"--- Email {i} ---\n"
            f"Absender: {e['sender']}\n"
            f"Betreff: {e['subject']}\n"
            f"Inhalt: {e['body'][:800]}\n"
        )

    email_block = "".join(email_texts)
    prompt = (
        f"Du analysierst Newsletter-Emails aus dem Zeitraum: {period_label}.\n\n"
        f"Hier sind {len(emails)} Emails:\n\n"
        f"{email_block}\n\n"
        "Erstelle ein Themen-Ranking als strukturierten HTML-Report (nur den <body>-Inhalt).\n\n"
        "Der Report soll enthalten:\n"
        "1. Ueberschrift mit Zeitraum und Anzahl analysierter Emails\n"
        "2. Top-10-Themenliste (nummeriert, absteigend nach Haeufigkeit):\n"
        "   - Themenname (praegnant, 2-5 Woerter)\n"
        "   - Haeufigkeit (Anzahl Emails, die das Thema erwaehnen)\n"
        "   - Kurze Beschreibung (1-2 Saetze)\n"
        "   - Beispiel-Newsletter/Absender\n"
        "3. Kurze Zusammenfassung der wichtigsten Trends (3-5 Saetze)\n\n"
        "Verwende einfaches HTML mit inline-Styles.\n"
        "Farben: Hintergrund weiss, Ueberschriften dunkelblau (#1a3a5c), Zebrastreifen (#f5f5f5).\n"
        "Antworte NUR mit dem HTML-Inhalt, kein Markdown, keine Erklaerungen."
    )

    import time
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/MSanderVD/Newsletter-Analyse",
    }
    payload = {
        "model": "mistralai/mistral-7b-instruct:free",
        "messages": [{"role": "user", "content": prompt}],
    }
    for attempt in range(3):
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        if resp.status_code == 429:
            wait = 60 * (attempt + 1)
            logger.info(f"OpenRouter 429 – warte {wait} Sekunden (Versuch {attempt+1}/3)")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break

    data = resp.json()
    if "choices" not in data:
        logger.error(f"OpenRouter Antwort ohne choices: {data}")
        raise ValueError(f"OpenRouter Fehler: {data.get('error', data)}")
    return data["choices"][0]["message"]["content"]

# ---------------------------------------------------------------------------
# OneDrive (Microsoft Graph, kostenlos mit Microsoft-Konto)
# ---------------------------------------------------------------------------

def _get_onedrive_token() -> str:
    url = (
        f"https://login.microsoftonline.com/{os.environ['AZURE_TENANT_ID']}"
        "/oauth2/v2.0/token"
    )
    resp = requests.post(url, data={
        "grant_type":    "client_credentials",
        "client_id":     os.environ["AZURE_CLIENT_ID"],
        "client_secret": os.environ["AZURE_CLIENT_SECRET"],
        "scope":         "https://graph.microsoft.com/.default",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def upload_to_onedrive(html_content: str, filename: str):
    token  = _get_onedrive_token()
    user   = os.environ["ONEDRIVE_USER_EMAIL"]
    folder = os.environ.get("ONEDRIVE_FOLDER_PATH", "Newsletter-Analysen")

    url = (
        f"https://graph.microsoft.com/v1.0/users/{user}"
        f"/drive/root:/{folder}/{filename}:/content"
    )
    resp = requests.put(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/html; charset=utf-8"},
        data=html_content.encode("utf-8"),
        timeout=60,
    )
    resp.raise_for_status()
    logger.info(f"OneDrive-Upload: {folder}/{filename}")


# ---------------------------------------------------------------------------
# Hauptablauf
# ---------------------------------------------------------------------------

def run_analysis(days_back: int, period_label: str, report_subject: str):
    recipient = os.environ["REPORT_RECIPIENT_EMAIL"]
    logger.info(f"Starte: {period_label} ({days_back} Tage)")

    service = get_gmail_service()
    emails  = fetch_emails(service, days_back)

    if not emails:
        logger.warning("Keine Emails – Analyse übersprungen.")
        return

    html_body = analyse_with_gemini(emails, period_label)

    now_str   = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    full_html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Newsletter-Analyse: {period_label}</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }}
    .footer {{ margin-top: 40px; font-size: 12px; color: #888; border-top: 1px solid #ddd; padding-top: 12px; }}
  </style>
</head>
<body>
{html_body}
<div class="footer">
  Automatisch erstellt am {now_str} · vdnewsletteranalyse@gmail.com · {len(emails)} Emails analysiert
</div>
</body>
</html>"""

    send_email(service, recipient, report_subject, full_html)

    safe  = re.sub(r"[^a-zA-Z0-9_-]", "_", period_label)
    date  = datetime.datetime.now().strftime("%Y-%m-%d")
    upload_to_onedrive(full_html, f"Newsletter-Analyse_{safe}_{date}.html")

    logger.info(f"Fertig: {period_label}")


# ---------------------------------------------------------------------------
# CLI-Einstieg (wird von GitHub Actions aufgerufen)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly", "monthly"], required=True)
    args = parser.parse_args()

    today = datetime.date.today()

    if args.mode == "daily":
        run_analysis(
            days_back=1,
            period_label="Tagesanalyse",
            report_subject=f"📊 Newsletter-Themen heute – {today.strftime('%d.%m.%Y')}",
        )
    elif args.mode == "weekly":
        week = today.isocalendar()[1]
        run_analysis(
            days_back=7,
            period_label=f"Wochenanalyse KW {week}",
            report_subject=f"📊 Newsletter-Themen KW {week} – {today.strftime('%d.%m.%Y')}",
        )
    elif args.mode == "monthly":
        month = today.strftime("%B %Y")
        run_analysis(
            days_back=30,
            period_label=f"Monatsanalyse {month}",
            report_subject=f"📊 Newsletter-Themen {month}",
        )
