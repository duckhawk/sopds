"""OPDS Basic-auth -> Keycloak (ROPC) bridge: oidc.authenticate_password and
the BasicAuthMiddleware fallback. Keycloak HTTP calls are mocked."""
import base64

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from constance import config

from sopds_web_backend import oidc


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


@pytest.fixture
def oidc_on(db):
    config.SOPDS_AUTH = True
    config.SOPDS_OIDC_ENABLE = True
    config.SOPDS_OIDC_ISSUER = 'https://kc.example.com/realms/library'
    config.SOPDS_OIDC_CLIENT_ID = 'sopds'
    config.SOPDS_OIDC_CLIENT_SECRET = 'secret'
    cache.clear()


@pytest.fixture
def discovery(monkeypatch):
    monkeypatch.setattr(oidc, '_discovery', lambda: {
        'token_endpoint': 'https://kc/t', 'userinfo_endpoint': 'https://kc/u'})


@pytest.mark.django_db
def test_ropc_success_provisions_user(oidc_on, discovery, monkeypatch):
    monkeypatch.setattr(oidc.requests, 'post', lambda *a, **k: _Resp(200, {'access_token': 'AT'}))
    monkeypatch.setattr(oidc.requests, 'get', lambda *a, **k: _Resp(200, {'preferred_username': 'reader', 'email': 'r@x.y'}))
    user = oidc.authenticate_password('reader', 'pw')
    assert user is not None and user.username == 'reader' and user.is_active


@pytest.mark.django_db
def test_ropc_bad_credentials(oidc_on, discovery, monkeypatch):
    monkeypatch.setattr(oidc.requests, 'post', lambda *a, **k: _Resp(401, {'error': 'invalid_grant'}))
    assert oidc.authenticate_password('reader', 'wrong') is None


@pytest.mark.django_db
def test_ropc_disabled_returns_none(db):
    config.SOPDS_OIDC_ENABLE = False
    assert oidc.authenticate_password('reader', 'pw') is None


@pytest.mark.django_db
def test_ropc_denies_staff(oidc_on, discovery, monkeypatch):
    User.objects.create_user('boss', 'b@x.y', 'pw', is_staff=True)
    monkeypatch.setattr(oidc.requests, 'post', lambda *a, **k: _Resp(200, {'access_token': 'AT'}))
    monkeypatch.setattr(oidc.requests, 'get', lambda *a, **k: _Resp(200, {'preferred_username': 'boss'}))
    assert oidc.authenticate_password('boss', 'pw') is None


@pytest.mark.django_db
def test_opds_feed_basic_auth_via_ropc(client, oidc_on, monkeypatch):
    kc_user = User.objects.create_user('kcreader', 'k@x.y', '!unusable')
    monkeypatch.setattr(oidc, 'authenticate_password', lambda u, p: kc_user)
    hdr = 'Basic ' + base64.b64encode(b'kcreader:kcpassword').decode()
    resp = client.get('/opds/', HTTP_AUTHORIZATION=hdr)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_opds_feed_basic_auth_rejected(client, oidc_on, monkeypatch):
    monkeypatch.setattr(oidc, 'authenticate_password', lambda u, p: None)
    hdr = 'Basic ' + base64.b64encode(b'nobody:bad').decode()
    resp = client.get('/opds/', HTTP_AUTHORIZATION=hdr)
    assert resp.status_code == 401
