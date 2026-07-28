"""OIDC (Keycloak) web login, configured entirely from the admin (constance).

An Authlib client is built lazily from the constance settings and rebuilt when
they change. Endpoints are discovered from the issuer's
``.well-known/openid-configuration`` (so only the issuer URL is configured, not
each endpoint).

Browser login uses the OIDC redirect flow. OPDS feeds (e-readers) can't do an
interactive redirect, so they send HTTP Basic auth; authenticate_password()
below validates those credentials against Keycloak via the Resource Owner
Password Credentials grant (requires "Direct Access Grants" enabled on the
Keycloak client; no MFA).

Administrator rights come from the IdP when ``SOPDS_OIDC_ADMIN_ROLE`` names a
Keycloak role or group; see provision_user() for exactly who is promoted,
demoted and refused. With the setting empty — the default — OIDC provisions
regular users only and administrators sign in with a local password.
"""
import base64
import hashlib
import json

import requests
from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from constance import config

# Records that an account's staff/superuser flags were granted here, from the
# IdP, rather than set by hand in the Django admin. Only what this module gave
# does it take away again, so revoking the Keycloak role demotes the account
# instead of locking it out, and a locally made administrator is never touched.
OIDC_ADMIN_GROUP = 'oidc-admins'

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


def _unverified_claims(token):
    """Claims of a JWT, without verifying its signature.

    Used for one thing: reading role claims out of an access token this server
    fetched itself from the token endpoint, over TLS, in a back-channel request
    — a token that never passed through the browser, so its contents are as
    trustworthy as the response it arrived in. Nothing about *identity* is ever
    taken from here; that comes from the ID token Authlib verified, or from the
    userinfo endpoint.

    The fallback exists because Keycloak puts realm roles in the access token
    by default and in the ID token and userinfo only once a mapper says so, so
    without it the common configuration silently grants nobody anything.
    """
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)      # JWT strips base64 padding
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (AttributeError, IndexError, TypeError, ValueError):
        return {}                                 # opaque or malformed: no roles
    return claims if isinstance(claims, dict) else {}


def _role_names(userinfo, access_token=None):
    """Every role and group name the IdP asserted, however it was configured.

    Keycloak has three places to put them and picks by which mapper is on:
    realm roles in ``realm_access.roles``, client roles in
    ``resource_access.<client>.roles``, and group membership in ``groups`` — the
    last as a path, ``/staff/librarians``. Both the whole path without its
    leading slash and its last segment count as a name, so the setting can be
    written either way.
    """
    claims = dict(_unverified_claims(access_token)) if access_token else {}
    claims.update(userinfo or {})

    raw = set()
    raw.update((claims.get('realm_access') or {}).get('roles') or [])
    for client in (claims.get('resource_access') or {}).values():
        if isinstance(client, dict):
            raw.update(client.get('roles') or [])
    for key in ('roles', 'groups'):
        value = claims.get(key)
        raw.update([value] if isinstance(value, str) else (value or []))

    names = set()
    for name in raw:
        if isinstance(name, str):
            names.add(name.lstrip('/'))
            names.add(name.rsplit('/', 1)[-1])
    return names


def _admin_group():
    return Group.objects.get_or_create(name=OIDC_ADMIN_GROUP)[0]


def provision_user(userinfo, access_token=None):
    """Map OIDC claims to a Django user, creating it on first login.

    Returns the user, or None to deny login. Users are created active.

    ``SOPDS_OIDC_ADMIN_ROLE`` decides who administers the catalogue:

    * Empty (the default) — nobody is promoted, and a login is refused when the
      username already belongs to a staff or superuser account, so an
      administrator cannot be impersonated by whoever can pick a username in
      the IdP. Administrators sign in with a local password.
    * Set — an account whose claims carry that role becomes staff and
      superuser, and one this module promoted becomes a regular user again when
      the role goes away. An administrator made locally is still refused when
      the IdP does not call them one: their flags are not the IdP's to revoke,
      and letting the login through would hand their account to a namesake.

    Which means: with the role configured, whoever can grant it in Keycloak can
    administer this catalogue. That is the point of the setting, and the reason
    it is empty by default.
    """
    username = (userinfo.get('preferred_username')
                or userinfo.get('email')
                or userinfo.get('sub') or '').strip()
    if not username:
        return None

    email = userinfo.get('email') or ''

    admin_role = (config.SOPDS_OIDC_ADMIN_ROLE or '').strip()
    is_admin = bool(admin_role) and admin_role in _role_names(userinfo, access_token)

    existing = User.objects.filter(username=username).first()
    if existing and (existing.is_staff or existing.is_superuser) and not is_admin:
        # Refuse the locally made administrator; demote the one whose role was
        # revoked. Group membership is the only thing that tells them apart.
        if not existing.groups.filter(name=OIDC_ADMIN_GROUP).exists():
            return None

    user, created = User.objects.get_or_create(
        username=username,
        defaults={'email': email, 'is_staff': is_admin, 'is_superuser': is_admin},
    )
    if created and is_admin:
        user.groups.add(_admin_group())

    updates = []
    if email and user.email != email:
        user.email = email
        updates.append('email')
    if not user.is_active:
        user.is_active = True
        updates.append('is_active')
    if admin_role and not created:
        was_admin = user.is_staff or user.is_superuser
        if is_admin and not was_admin:
            user.is_staff = user.is_superuser = True
            updates += ['is_staff', 'is_superuser']
            user.groups.add(_admin_group())
        elif was_admin and not is_admin:
            # Only reachable for an account promoted here — the check above
            # refused every other administrator.
            user.is_staff = user.is_superuser = False
            updates += ['is_staff', 'is_superuser']
            user.groups.remove(_admin_group())
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
        # Same rule as provision_user(), which wrote this entry: an
        # administrator the IdP granted may sign in, one made locally may not.
        if user and (not (user.is_staff or user.is_superuser)
                     or user.groups.filter(name=OIDC_ADMIN_GROUP).exists()):
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

    user = provision_user(userinfo, access_token)
    if user is not None:
        cache.set(cache_key, user.id, 300)
    return user
