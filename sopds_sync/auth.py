"""Shared authentication helpers for the sync endpoints.

Two protocols, two auth schemes:

* KOReader (kosync) sends its own ``x-auth-user`` / ``x-auth-key`` headers,
  where the key is md5(password). Validated against :class:`KosyncCredential`.
* Moon+ Reader (WebDAV) sends ordinary HTTP Basic with a plaintext password,
  so it reuses the same path as the OPDS feeds: local ``auth.authenticate`` and,
  failing that, Keycloak ROPC via ``oidc.authenticate_password``.
"""
import base64
import binascii
import hmac

from django.contrib import auth
from django.http import HttpResponse

from .models import KosyncCredential


def authenticate_kosync(request):
    """Return the active User for a valid kosync header pair, else ``None``."""
    username = request.META.get('HTTP_X_AUTH_USER')
    auth_key = request.META.get('HTTP_X_AUTH_KEY')
    if not username or not auth_key:
        return None
    cred = (KosyncCredential.objects
            .filter(user__username=username, user__is_active=True)
            .select_related('user')
            .first())
    if cred is None:
        return None
    # Constant-time compare so a wrong key can't be recovered by timing.
    if not hmac.compare_digest(cred.auth_key, auth_key):
        return None
    return cred.user


def authenticate_basic(request):
    """Return the active User for valid HTTP Basic credentials, else ``None``.

    Mirrors ``opds_catalog.middleware.BasicAuthMiddleware``: try a local account
    first, then fall back to Keycloak's ROPC grant for OIDC-only users. Does not
    call ``login()`` — WebDAV clients re-send Basic auth on every request, so a
    session row per request would grow unbounded (same reasoning as OPDS feeds).
    """
    authorization = request.META.get('HTTP_AUTHORIZATION')
    if not authorization:
        return None
    try:
        auth_meth, auth_data = authorization.split(' ', 1)
    except ValueError:
        return None
    if auth_meth.lower() != 'basic':
        return None
    try:
        decoded = base64.b64decode(auth_data.strip()).decode('utf-8')
        username, password = decoded.split(':', 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None

    user = auth.authenticate(username=username, password=password)
    if not (user and user.is_active):
        from sopds_web_backend import oidc
        if oidc.oidc_enabled():
            user = oidc.authenticate_password(username, password)

    if user and user.is_active:
        return user
    return None


def basic_auth_challenge(realm='SOPDS WebDAV'):
    """A 401 response asking the client for HTTP Basic credentials."""
    response = HttpResponse(status=401)
    response['WWW-Authenticate'] = 'Basic realm="%s"' % realm
    return response
