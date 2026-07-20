# -*- coding: utf-8 -*-
"""
Per-user Google Drive sync of reading positions with Moon+ Reader.

Moon+ Reader stores a per-book position file in a Drive folder (default
"Books/.Moon+/Cache", configurable via SOPDS_GDRIVE_PATH) named
    "<title> - <author full_name>.<format>.zip.po"
with the content format
    "<timestamp_ms>*<chapter>@<volume>#<char_offset>:<percent>%".

Only the percentage is portable between apps, so we sync on it. All Drive
errors are swallowed and logged - a broken sync must never break the reader.

Google client libraries are imported lazily so the app still runs where they
are not installed (e.g. some local dev setups).
"""
import datetime
import logging
import os
import re
import time

# Google may return extra granted scopes (openid/email); relax so the OAuth
# library does not raise "Scope has changed" during token exchange.
os.environ.setdefault('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')

from django.conf import settings

from constance import config

from opds_catalog.models import GDriveAccount

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
TOKEN_URI = 'https://oauth2.googleapis.com/token'
FOLDER_MIME = 'application/vnd.google-apps.folder'

PO_PERCENT_RE = re.compile(r':([0-9]+(?:\.[0-9]+)?)%\s*$')


def is_configured():
    """True if an application OAuth client is provided."""
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


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


def _service(account):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=None,
        refresh_token=account.refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=SCOPES,
    )
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def account_email(refresh_token):
    """Fetch the connected account's email (best effort) right after connect."""
    tmp = GDriveAccount(refresh_token=refresh_token)
    try:
        about = _service(tmp).about().get(fields='user(emailAddress)').execute()
        return (about.get('user') or {}).get('emailAddress')
    except Exception:
        logger.exception('gdrive: could not read account email')
        return None


# --- filename / path helpers -------------------------------------------------

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


def _find_child_folder(service, parent_id, name, create):
    q = ("mimeType='%s' and trashed=false and name='%s' and '%s' in parents"
         % (FOLDER_MIME, _q_escape(name), parent_id))
    resp = service.files().list(q=q, spaces='drive', fields='files(id)', pageSize=1).execute()
    files = resp.get('files', [])
    if files:
        return files[0]['id']
    if not create:
        return None
    meta = {'name': name, 'mimeType': FOLDER_MIME, 'parents': [parent_id]}
    return service.files().create(body=meta, fields='id').execute()['id']


def _resolve_cache_folder(service, account, create):
    if account.cache_folder_id:
        return account.cache_folder_id
    parts = [p for p in (config.SOPDS_GDRIVE_PATH or '').strip('/').split('/') if p]
    parent = 'root'
    for name in parts:
        parent = _find_child_folder(service, parent, name, create)
        if not parent:
            return None
    if parent and parent != 'root':
        account.cache_folder_id = parent
        account.save(update_fields=['cache_folder_id'])
    return parent


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
    if not account or not is_configured():
        return None
    try:
        service = _service(account)
        folder = _resolve_cache_folder(service, account, create=False)
        if not folder:
            return None
        f = _find_po(service, folder, po_filename(book))
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
    if not account or not is_configured():
        return False
    try:
        service = _service(account)
        folder = _resolve_cache_folder(service, account, create=True)
        if not folder:
            return False
        filename = po_filename(book)
        existing = _find_po(service, folder, filename)
        media = MediaInMemoryUpload(_build_po(percent, chapter).encode('utf-8'),
                                    mimetype='text/plain', resumable=False)
        if existing:
            service.files().update(fileId=existing['id'], media_body=media).execute()
        else:
            service.files().create(body={'name': filename, 'parents': [folder]},
                                   media_body=media, fields='id').execute()
        return True
    except Exception:
        logger.exception('gdrive: push failed for book %s', getattr(book, 'id', '?'))
        return False
