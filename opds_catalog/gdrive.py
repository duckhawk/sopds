# -*- coding: utf-8 -*-
"""
Per-user Google Drive sync of reading positions with Moon+ Reader.

Uses the least-privilege ``drive.file`` scope: the app can only touch files it
created or files/folders the user explicitly grants via the Google Picker. The
user picks their Moon+ Reader cache folder (default location "Books/.Moon+/Cache")
once; after that the app can read/write the per-book position files inside it and
nothing else in the user's Drive.

Position file (Moon+ Reader format), named
    "<title> - <author full_name>.<format>.zip.po"
with content
    "<timestamp_ms>*<chapter>@<volume>#<char_offset>:<percent>%".
Only the percentage is portable, so we sync on it. All Drive errors are
swallowed and logged - a broken sync must never break the reader.

Google client libraries are imported lazily so the app still runs where they
are not installed.
"""
import datetime
import logging
import os
import re
import time

# Google may return extra granted scopes; relax so the OAuth library does not
# raise "Scope has changed" during token exchange.
os.environ.setdefault('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')

from django.conf import settings

from opds_catalog.models import GDriveAccount

logger = logging.getLogger(__name__)

# drive.file: per-file access, limited to what the app creates or the user picks
# via the Google Picker. This is what keeps access scoped to the chosen folder.
SCOPES = ['https://www.googleapis.com/auth/drive.file']
TOKEN_URI = 'https://oauth2.googleapis.com/token'

PO_PERCENT_RE = re.compile(r':([0-9]+(?:\.[0-9]+)?)%\s*$')


def is_configured():
    """True if an application OAuth client is provided."""
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def client_id():
    return settings.GOOGLE_OAUTH_CLIENT_ID


def api_key():
    return settings.GOOGLE_API_KEY


def _client_config():
    return {
        "web": {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
        }
    }


def build_flow(redirect_uri, state=None):
    """OAuth flow for the connect/callback dance."""
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(_client_config(), scopes=SCOPES,
                                   redirect_uri=redirect_uri, state=state)


def _credentials(account):
    from google.oauth2.credentials import Credentials
    return Credentials(
        token=None,
        refresh_token=account.refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=SCOPES,
    )


def _service(account):
    from googleapiclient.discovery import build
    return build('drive', 'v3', credentials=_credentials(account), cache_discovery=False)


def picker_access_token(account):
    """Mint a short-lived access token from the stored refresh token, for the
    browser-side Google Picker (avoids a second in-browser OAuth consent)."""
    from google.auth.transport.requests import Request
    creds = _credentials(account)
    try:
        creds.refresh(Request())
        return creds.token
    except Exception:
        logger.exception('gdrive: could not mint picker access token')
        return None


def account_email(refresh_token):
    """Fetch the connected account's email (best effort) right after connect."""
    tmp = GDriveAccount(refresh_token=refresh_token)
    try:
        about = _service(tmp).about().get(fields='user(emailAddress)').execute()
        return (about.get('user') or {}).get('emailAddress')
    except Exception:
        logger.exception('gdrive: could not read account email')
        return None


# --- filename / file helpers -------------------------------------------------

def po_filename(book):
    """Build the .po filename Moon+ Reader uses for this book."""
    author = book.authors.first()
    base = book.title
    if author and author.full_name:
        base = '%s - %s' % (book.title, author.full_name)
    fmt = (book.format or 'fb2').lower()
    return '%s.%s.zip.po' % (base, fmt)


def _q_escape(value):
    return value.replace('\\', '\\\\').replace("'", "\\'")


def _find_po(service, folder_id, filename):
    q = "name='%s' and '%s' in parents and trashed=false" % (_q_escape(filename), folder_id)
    resp = service.files().list(q=q, spaces='drive',
                                fields='files(id,name,modifiedTime)', pageSize=1).execute()
    files = resp.get('files', [])
    return files[0] if files else None


def _parse_percent(text):
    m = PO_PERCENT_RE.search((text or '').strip())
    return float(m.group(1)) if m else None


def _parse_mtime(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _build_po(percent, chapter=0):
    ts = int(time.time() * 1000)
    return '%d*%d@0#0:%.1f%%' % (ts, int(chapter or 0), float(percent))


# --- high-level API used by the views ---------------------------------------

def pull_position(user, book):
    """Read the position percentage from Drive. Returns {'percent', 'mtime'} or None."""
    account = GDriveAccount.objects.filter(user=user).first()
    if not account or not account.cache_folder_id or not is_configured():
        return None
    try:
        service = _service(account)
        f = _find_po(service, account.cache_folder_id, po_filename(book))
        if not f:
            return None
        data = service.files().get_media(fileId=f['id']).execute()
        text = data.decode('utf-8', 'replace') if isinstance(data, (bytes, bytearray)) else str(data)
        percent = _parse_percent(text)
        if percent is None:
            return None
        return {'percent': percent, 'mtime': _parse_mtime(f.get('modifiedTime'))}
    except Exception:
        logger.exception('gdrive: pull failed for book %s', getattr(book, 'id', '?'))
        return None


def push_position(user, book, percent, chapter=0):
    """Write the position percentage to Drive. Returns True on success."""
    from googleapiclient.http import MediaInMemoryUpload
    account = GDriveAccount.objects.filter(user=user).first()
    if not account or not account.cache_folder_id or not is_configured():
        return False
    try:
        service = _service(account)
        filename = po_filename(book)
        existing = _find_po(service, account.cache_folder_id, filename)
        media = MediaInMemoryUpload(_build_po(percent, chapter).encode('utf-8'),
                                    mimetype='text/plain', resumable=False)
        if existing:
            service.files().update(fileId=existing['id'], media_body=media).execute()
        else:
            service.files().create(body={'name': filename, 'parents': [account.cache_folder_id]},
                                   media_body=media, fields='id').execute()
        return True
    except Exception:
        logger.exception('gdrive: push failed for book %s', getattr(book, 'id', '?'))
        return False
