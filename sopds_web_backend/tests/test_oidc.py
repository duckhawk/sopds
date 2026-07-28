"""OIDC (Keycloak) login: config toggle, login-page button, user provisioning
and the callback flow (Authlib client mocked — no real IdP needed)."""
import base64
import json

import pytest
from django.urls import reverse
from django.contrib.auth.models import User
from constance import config

from sopds_web_backend import oidc


def fake_access_token(claims):
    """A JWT-shaped string carrying `claims`, unsigned — the role fallback
    reads the payload and never checks the signature."""
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip('=')
    return '%s.%s.%s' % (seg({'alg': 'RS256'}), seg(claims), 'signature')


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


# --- Administrator rights from a Keycloak role (SOPDS_OIDC_ADMIN_ROLE) -------

@pytest.fixture
def admin_role(db):
    config.SOPDS_OIDC_ADMIN_ROLE = 'lectern-admin'
    yield 'lectern-admin'
    config.SOPDS_OIDC_ADMIN_ROLE = ''


@pytest.mark.django_db
def test_role_in_userinfo_grants_admin(admin_role):
    user = oidc.provision_user({'preferred_username': 'chief',
                                'realm_access': {'roles': ['lectern-admin', 'offline_access']}})
    assert user.is_staff and user.is_superuser
    assert user.groups.filter(name=oidc.OIDC_ADMIN_GROUP).exists()


@pytest.mark.django_db
def test_role_only_in_access_token_grants_admin(admin_role):
    """Keycloak's default: realm roles ride in the access token, and reach the
    ID token or userinfo only once a mapper is configured."""
    token = fake_access_token({'realm_access': {'roles': ['lectern-admin']}})
    user = oidc.provision_user({'preferred_username': 'chief'}, token)
    assert user.is_staff and user.is_superuser


@pytest.mark.django_db
def test_group_path_matches_by_full_path_and_leaf(admin_role):
    config.SOPDS_OIDC_ADMIN_ROLE = 'librarians'
    leaf = oidc.provision_user({'preferred_username': 'a', 'groups': ['/staff/librarians']})
    assert leaf.is_superuser

    config.SOPDS_OIDC_ADMIN_ROLE = 'staff/librarians'
    full = oidc.provision_user({'preferred_username': 'b', 'groups': ['/staff/librarians']})
    assert full.is_superuser


@pytest.mark.django_db
def test_client_role_grants_admin(admin_role):
    user = oidc.provision_user({'preferred_username': 'chief',
                                'resource_access': {'sopds': {'roles': ['lectern-admin']}}})
    assert user.is_superuser


@pytest.mark.django_db
def test_opaque_access_token_is_ignored(admin_role):
    user = oidc.provision_user({'preferred_username': 'plain'}, 'not-a-jwt')
    assert user is not None and not user.is_staff


@pytest.mark.django_db
def test_without_the_role_no_admin(admin_role):
    user = oidc.provision_user({'preferred_username': 'reader',
                                'realm_access': {'roles': ['offline_access']}})
    assert not user.is_staff and not user.is_superuser


@pytest.mark.django_db
def test_revoking_the_role_demotes_instead_of_locking_out(admin_role):
    granted = oidc.provision_user({'preferred_username': 'chief',
                                   'realm_access': {'roles': ['lectern-admin']}})
    assert granted.is_superuser

    demoted = oidc.provision_user({'preferred_username': 'chief', 'realm_access': {'roles': []}})
    assert demoted is not None                 # signs in, as a regular reader
    assert not demoted.is_staff and not demoted.is_superuser
    assert not demoted.groups.filter(name=oidc.OIDC_ADMIN_GROUP).exists()


@pytest.mark.django_db
def test_local_admin_without_the_role_is_still_refused(admin_role):
    """The flags were not the IdP's to grant, so they are not its to revoke —
    and letting the login through would hand the account to a namesake."""
    User.objects.create_user('boss', 'b@x.y', 'pw', is_superuser=True)
    assert oidc.provision_user({'preferred_username': 'boss',
                                'realm_access': {'roles': ['offline_access']}}) is None


@pytest.mark.django_db
def test_local_admin_with_the_role_signs_in_unmarked(admin_role):
    User.objects.create_user('boss', 'b@x.y', 'pw', is_staff=True, is_superuser=True)
    user = oidc.provision_user({'preferred_username': 'boss',
                                'realm_access': {'roles': ['lectern-admin']}})
    assert user is not None and user.is_superuser
    # Not marked as IdP-granted: a later revocation must not demote an account
    # whose rights this module never gave it.
    assert not user.groups.filter(name=oidc.OIDC_ADMIN_GROUP).exists()
