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
