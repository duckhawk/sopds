"""OIDC (Keycloak) web login, configured entirely from the admin (constance).

An Authlib client is built lazily from the constance settings and rebuilt when
they change. Endpoints are discovered from the issuer's
``.well-known/openid-configuration`` (so only the issuer URL is configured, not
each endpoint).

Browser login uses the OIDC redirect flow. OPDS feeds (e-readers) can't do an
interactive redirect, so they send HTTP Basic auth; authenticate_password()
below validates those credentials against Keycloak via the Resource Owner
Password Credentials grant (requires "Direct Access Grants" enabled on the
Keycloak client; no MFA). Django admin access stays with local accounts —
OIDC provisions regular (non-staff) users, and login is refused for usernames
that already belong to a staff/superuser account.
"""
import hashlib

import requests
from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from constance import config

_oauth = None
_signature = None


def oidc_enabled():
    """True when OIDC is switched on and minimally configured."""
    return bool(
        config.SOPDS_OIDC_ENABLE
        and config.SOPDS_OIDC_ISSUER
        and config.SOPDS_OIDC_CLIENT_ID
    )


def get_client():
    """Return the Authlib OIDC client, (re)built from constance on change."""
    global _oauth, _signature
    issuer = config.SOPDS_OIDC_ISSUER.rstrip('/')
    scopes = config.SOPDS_OIDC_SCOPES or 'openid email profile'
    sig = (issuer, config.SOPDS_OIDC_CLIENT_ID, config.SOPDS_OIDC_CLIENT_SECRET, scopes)
    if _oauth is None or _signature != sig:
        oauth = OAuth()
        oauth.register(
            name='keycloak',
            client_id=config.SOPDS_OIDC_CLIENT_ID,
            client_secret=config.SOPDS_OIDC_CLIENT_SECRET,
            server_metadata_url='%s/.well-known/openid-configuration' % issuer,
            client_kwargs={'scope': scopes},
        )
        _oauth = oauth
        _signature = sig
    return _oauth.keycloak


def provision_user(userinfo):
    """Map OIDC claims to a Django user, creating it on first login.

    Returns the user, or None to deny login. Users are created active and
    non-staff. Login is refused when the mapped username already belongs to a
    staff/superuser account, so an admin account can never be taken over via
    the IdP (admins sign in locally).
    """
    username = (userinfo.get('preferred_username')
                or userinfo.get('email')
                or userinfo.get('sub') or '').strip()
    if not username:
        return None

    email = userinfo.get('email') or ''

    existing = User.objects.filter(username=username).first()
    if existing and (existing.is_staff or existing.is_superuser):
        return None

    user, created = User.objects.get_or_create(
        username=username,
        defaults={'email': email, 'is_staff': False, 'is_superuser': False},
    )

    updates = []
    if email and user.email != email:
        user.email = email
        updates.append('email')
    if not user.is_active:
        user.is_active = True
        updates.append('is_active')
    if updates:
        user.save(update_fields=updates)

    return user


def _discovery():
    """Fetch (and cache for an hour) the issuer's OIDC discovery document."""
    issuer = config.SOPDS_OIDC_ISSUER.rstrip('/')
    key = 'oidc:discovery:%s' % issuer
    doc = cache.get(key)
    if doc is None:
        resp = requests.get('%s/.well-known/openid-configuration' % issuer, timeout=10)
        resp.raise_for_status()
        doc = resp.json()
        cache.set(key, doc, 3600)
    return doc


def authenticate_password(username, password):
    """Validate OPDS Basic-auth credentials against Keycloak (ROPC grant).

    Returns the provisioned Django user, or None. A successful result is cached
    briefly (keyed by a salted hash of the credentials, never the password
    itself) so e-readers — which re-send Basic auth on every request — don't hit
    Keycloak each time.
    """
    if not oidc_enabled() or not username or not password:
        return None

    cache_key = 'oidc:ropc:%s' % hashlib.sha256(
        ('%s:%s:%s' % (username, password, settings.SECRET_KEY)).encode()).hexdigest()
    cached_uid = cache.get(cache_key)
    if cached_uid:
        user = User.objects.filter(id=cached_uid, is_active=True).first()
        if user and not (user.is_staff or user.is_superuser):
            return user

    try:
        doc = _discovery()
        data = {
            'grant_type': 'password',
            'client_id': config.SOPDS_OIDC_CLIENT_ID,
            'username': username,
            'password': password,
            'scope': config.SOPDS_OIDC_SCOPES or 'openid email profile',
        }
        if config.SOPDS_OIDC_CLIENT_SECRET:
            data['client_secret'] = config.SOPDS_OIDC_CLIENT_SECRET
        resp = requests.post(doc['token_endpoint'], data=data, timeout=10)
        if resp.status_code != 200:
            return None
        access_token = resp.json().get('access_token')
        if not access_token:
            return None
        # Validate the token and get claims from the userinfo endpoint (returns
        # 200 only for a valid token — the authoritative server-side check).
        ur = requests.get(doc['userinfo_endpoint'],
                          headers={'Authorization': 'Bearer %s' % access_token}, timeout=10)
        if ur.status_code != 200:
            return None
        userinfo = ur.json()
    except (requests.RequestException, KeyError, ValueError):
        return None

    user = provision_user(userinfo)
    if user is not None:
        cache.set(cache_key, user.id, 300)
    return user
