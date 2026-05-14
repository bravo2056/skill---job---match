"""
run-scan.py - entry point for the script-orchestrated email scanner.

Wires Gmail OAuth to email_scanner.scan_inbox():
  1. Load/refresh OAuth token (browser consent on first run)
  2. List threads under label "Job search 2026"
  3. Pull subject/sender/date headers per thread
  4. Hand the threads list + gmail client to scan_inbox()
  5. event_log callback shells out to integrity.py event_log_write

Outputs:
  scan-staging.json, rejection-staging.json, unparsed-staging.json,
  scan-staging.json.complete (sentinel)

Then a separate Phase 2 (agent or script) reads scan-staging.json and ingests.
"""
import datetime
import importlib.util
import json
import os
import subprocess
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---------- Paths ----------
CAREER_DIR = r'C:/Users/Garrison/career'
CREDENTIALS_PATH = os.path.join(CAREER_DIR, 'credentials.json')
TOKEN_PATH = os.path.join(CAREER_DIR, 'token.json')
INTEGRITY_PATH = os.path.join(CAREER_DIR, 'integrity.py')
SCANNER_PATH = os.path.join(CAREER_DIR, 'email-scanner.py')

LABEL_NAME = 'Job search 2026'
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

SESSION_ID = 'scan_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')


def load_scanner_module():
    """Import email-scanner.py (hyphenated filename → importlib)."""
    spec = importlib.util.spec_from_file_location('email_scanner', SCANNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _restrict_token_acl(path):
    # Lock token.json to the current user only. Holds a Gmail refresh token
    # with gmail.modify scope; default Windows ACLs are too permissive for a
    # long-lived credential. Best-effort: missing icacls is non-fatal.
    try:
        import getpass
        user = getpass.getuser()
        subprocess.run(
            ['icacls', path, '/inheritance:r', '/grant:r', f'{user}:F'],
            capture_output=True, check=False,
        )
    except Exception:
        pass


def get_gmail_service():
    """OAuth flow: load token, refresh if expired, prompt browser if first run."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, 'w', encoding='utf-8') as f:
            f.write(creds.to_json())
        _restrict_token_acl(TOKEN_PATH)
    return build('gmail', 'v1', credentials=creds)


def find_label_id(gmail, name):
    labels = gmail.users().labels().list(userId='me').execute().get('labels', [])
    for lbl in labels:
        if lbl.get('name') == name:
            return lbl.get('id')
    raise SystemExit(f'Label not found: {name!r}')


def list_threads(gmail, label_id):
    """Page through all threads under label_id."""
    threads = []
    page_token = None
    while True:
        resp = gmail.users().threads().list(
            userId='me', labelIds=[label_id], pageToken=page_token, maxResults=100,
        ).execute()
        threads.extend(resp.get('threads', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return threads


def thread_headers(gmail, thread_id):
    """Fetch only the first message's metadata headers."""
    thread = gmail.users().threads().get(
        userId='me', id=thread_id, format='metadata',
        metadataHeaders=['Subject', 'From', 'Date'],
    ).execute()
    msgs = thread.get('messages', [])
    if not msgs:
        return None
    headers = {h['name']: h['value'] for h in msgs[0].get('payload', {}).get('headers', [])}
    raw_from = headers.get('From', '')
    sender = raw_from.split('<')[-1].rstrip('>').strip() if '<' in raw_from else raw_from.strip()
    raw_date = headers.get('Date', '')
    msg_date = ''
    if raw_date:
        try:
            from email.utils import parsedate_to_datetime
            msg_date = parsedate_to_datetime(raw_date).strftime('%Y-%m-%d')
        except Exception:
            msg_date = ''
    return {
        'thread_id': thread_id,
        'subject': headers.get('Subject', ''),
        'sender': sender.lower(),
        'msg_date': msg_date,
    }


def event_log(event_type, event_detail, result):
    """Shell out to integrity.py event_log_write — same path the agent uses."""
    payload = json.dumps({
        'agent_name': 'email-scanner',
        'session_id': SESSION_ID,
        'event_type': event_type,
        'event_detail': event_detail,
        'result': result,
    })
    try:
        subprocess.run(
            [sys.executable, INTEGRITY_PATH, '--action', 'event_log_write', '--payload', payload],
            check=False, capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        print(f'[event_log] failed to log {event_type}: {e}', file=sys.stderr)


def main():
    print(f'Session ID: {SESSION_ID}')
    print('Authenticating with Gmail...')
    gmail = get_gmail_service()

    print(f'Looking up label: {LABEL_NAME}')
    label_id = find_label_id(gmail, LABEL_NAME)

    print('Listing threads...')
    raw_threads = list_threads(gmail, label_id)
    print(f'  found {len(raw_threads)} threads')

    print('Fetching headers...')
    threads = []
    for t in raw_threads:
        h = thread_headers(gmail, t['id'])
        if h:
            threads.append(h)

    print(f'Running scan_inbox over {len(threads)} threads...')
    scanner = load_scanner_module()
    scanner.scan_inbox(gmail, threads, event_log)

    print('Done. Staging files written:')
    print(f'  {scanner.STAGING_PATH}')
    print(f'  {scanner.REJECTION_STAGING_PATH}')
    print(f'  {scanner.UNPARSED_STAGING_PATH}')
    print(f'Sentinel: {scanner.COMPLETION_SENTINEL}')


if __name__ == '__main__':
    main()
