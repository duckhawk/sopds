"""OIDC (Keycloak) login: config toggle, login-page button, user provisioning
and the callback flow (Authlib client mocked — no real IdP needed)."""
import pytest
from django.urls import reverse
from django.contrib.auth.models import User
from constance import config

from sopds_web_backend import oidc


@pytest.fixture
def oidc_on(db):
    config.SOPDS_OIDC_ENABLE = True
    config.SOPDS_OIDC_ISSUER = 'https://kc.example.com/realms/library'
    config.SOPDS_OIDC_CLIENT_ID = 'sopds'
    config.SOPDS_OIDC_CLIENT_SECRET = 'secret'


@pytest.mark.django_db
def test_oidc_enabled_toggle():
    config.SOPDS_OIDC_ENABLE = False
    assert oidc.oidc_enabled() is False
    config.SOPDS_OIDC_ENABLE = True
    config.SOPDS_OIDC_ISSUER = ''
    assert oidc.oidc_enabled() is False   # needs issuer + client id too
    config.SOPDS_OIDC_ISSUER = 'https://kc/realms/x'
    config.SOPDS_OIDC_CLIENT_ID = 'cid'
    assert oidc.oidc_enabled() is True


@pytest.mark.django_db
def test_login_page_button_visibility(client, oidc_on):
    resp = client.get(reverse('web:login'))
    assert resp.status_code == 200
    assert reverse('web:oidc_login') in resp.content.decode()

    config.SOPDS_OIDC_ENABLE = False
    resp = client.get(reverse('web:login'))
    assert reverse('web:oidc_login') not in resp.content.decode()


@pytest.mark.django_db
def test_oidc_login_404_when_disabled(client):
    config.SOPDS_OIDC_ENABLE = False
    assert client.get(reverse('web:oidc_login')).status_code == 404


@pytest.mark.django_db
def test_provision_creates_regular_user():
    user = oidc.provision_user({'preferred_username': 'alice', 'email': 'alice@example.com'})
    assert user is not None
    assert user.username == 'alice'
    assert user.email == 'alice@example.com'
    assert user.is_active and not user.is_staff and not user.is_superuser


@pytest.mark.django_db
def test_provision_denies_staff_takeover():
    User.objects.create_user('boss', 'boss@x.y', 'pw', is_staff=True)
    assert oidc.provision_user({'preferred_username': 'boss'}) is None


@pytest.mark.django_db
def test_provision_no_username_returns_none():
    assert oidc.provision_user({}) is None


@pytest.mark.django_db
def test_oidc_callback_provisions_and_logs_in(client, oidc_on, monkeypatch):
    class FakeClient:
        def authorize_access_token(self, request):
            return {'userinfo': {'preferred_username': 'kcuser', 'email': 'kc@example.com'}}

    monkeypatch.setattr(oidc, 'get_client', lambda: FakeClient())

    resp = client.get(reverse('web:oidc_callback'))
    assert resp.status_code == 302
    assert resp.url == reverse('web:main')
    assert User.objects.filter(username='kcuser').exists()
    assert '_auth_user_id' in client.session   # session is authenticated


@pytest.mark.django_db
def test_oidc_callback_denies_staff(client, oidc_on, monkeypatch):
    User.objects.create_user('admin2', 'a@x.y', 'pw', is_superuser=True)

    class FakeClient:
        def authorize_access_token(self, request):
            return {'userinfo': {'preferred_username': 'admin2'}}

    monkeypatch.setattr(oidc, 'get_client', lambda: FakeClient())
    resp = client.get(reverse('web:oidc_callback'))
    assert resp.status_code == 403
    assert '_auth_user_id' not in client.session
