"""OIDC (Keycloak) web login, configured entirely from the admin (constance).

An Authlib client is built lazily from the constance settings and rebuilt when
they change. Endpoints are discovered from the issuer's
``.well-known/openid-configuration`` (so only the issuer URL is configured, not
each endpoint).

Scope: the browser login flow only. OPDS feeds keep HTTP Basic auth (e-readers
cannot do an interactive redirect). Django admin access stays with local
accounts — OIDC provisions regular (non-staff) users, and login is refused for
usernames that already belong to a staff/superuser account.
"""
from authlib.integrations.django_client import OAuth
from django.contrib.auth.models import User
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
