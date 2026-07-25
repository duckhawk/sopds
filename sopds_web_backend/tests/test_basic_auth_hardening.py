"""A malformed HTTP Basic Authorization header must yield 401, not 500 (#50).

BasicAuthMiddleware decoded/split the credential blob without guarding, so bad
base64 (binascii.Error), non-utf8 bytes (UnicodeDecodeError) or a missing ':'
(ValueError) reached the client as a 500.
"""
import base64

import pytest
from constance import config


@pytest.fixture
def auth_on(db):
    config.SOPDS_AUTH = True
    config.SOPDS_OIDC_ENABLE = False


@pytest.mark.django_db
def test_basic_auth_creates_no_session(client, auth_on, django_user_model):
    # #43: OPDS clients re-send Basic auth every request; the middleware must
    # not persist a session row per request.
    from django.contrib.sessions.models import Session
    django_user_model.objects.create_user('reader', 'r@x.y', 'pw')
    hdr = 'Basic ' + base64.b64encode(b'reader:pw').decode()
    resp = client.get('/opds/', HTTP_AUTHORIZATION=hdr)
    assert resp.status_code == 200
    assert Session.objects.count() == 0
    assert 'sessionid' not in resp.cookies


@pytest.mark.django_db
@pytest.mark.parametrize("header", [
    "Basic !!!not-base64!!!",                                   # binascii.Error
    "Basic " + base64.b64encode(b'no-colon-here').decode(),    # ValueError (no ':')
    "Basic " + base64.b64encode(b'\xff\xfe:pw').decode(),       # UnicodeDecodeError
    "Basic",                                                    # no data at all
    "Bearer sometoken",                                         # wrong scheme
])
def test_malformed_authorization_returns_401(client, auth_on, header):
    resp = client.get('/opds/', HTTP_AUTHORIZATION=header)
    assert resp.status_code == 401
