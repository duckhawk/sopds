"""KOReader "kosync" progress-sync server, compatible with the protocol at
``koreader/plugins/kosync.koplugin``.

KOReader is pointed at this app's mount (e.g. ``https://host/kosync``) and calls
four routes below. The server is a plain key-value store keyed by
``(user, document-hash)``: the document hash is computed client-side and never
mapped to a catalog Book. Credentials are provisioned in the SOPDS web UI (see
:class:`sopds_sync.models.KosyncCredential`); open registration via
``/users/create`` is off unless ``SOPDS_KOSYNC_ALLOW_REGISTER`` is enabled.

All routes are CSRF-exempt (native clients can't carry a CSRF token) and return
404 while ``SOPDS_KOSYNC_ENABLE`` is off, so the feature is fully invisible when
disabled.
"""
import json
import re

from django.contrib.auth.models import User
from django.http import JsonResponse, Http404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from constance import config

from .auth import authenticate_kosync
from .models import KosyncCredential, KosyncProgress

_MD5_RE = re.compile(r'^[0-9a-fA-F]{1,32}$')


def _enabled():
    if not config.SOPDS_KOSYNC_ENABLE:
        raise Http404('kosync disabled')


def _unauthorized():
    return JsonResponse({'code': 2001, 'message': 'Unauthorized'}, status=401)


def _epoch(dt):
    return int(dt.timestamp())


@csrf_exempt
def users_create(request):
    """POST /users/create — optional self-registration for KOReader users.

    The client sends ``{username, password}`` where ``password`` is *already*
    md5-hashed by KOReader, so it is stored verbatim as the credential's
    ``auth_key``. To avoid anyone claiming another account's sync channel, this
    only ever targets an existing Django user that has no credential yet, and is
    gated behind ``SOPDS_KOSYNC_ALLOW_REGISTER`` (off by default; provision from
    the web UI instead).
    """
    _enabled()
    if request.method != 'POST':
        return JsonResponse({'code': 2001, 'message': 'Method not allowed'}, status=405)
    if not config.SOPDS_KOSYNC_ALLOW_REGISTER:
        return JsonResponse({'code': 2003, 'message': 'Registration is disabled'}, status=403)

    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'code': 2004, 'message': 'Invalid request'}, status=400)

    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()  # already md5 from the client
    if not username or not password:
        return JsonResponse({'code': 2004, 'message': 'Invalid request'}, status=400)

    user = User.objects.filter(username=username, is_active=True).first()
    # Never provision credentials for privileged accounts over this channel.
    if user is None or user.is_staff or user.is_superuser:
        return JsonResponse({'code': 2004, 'message': 'Invalid request'}, status=402)
    if KosyncCredential.objects.filter(user=user).exists():
        return JsonResponse({'code': 2002, 'message': 'Username is already registered.'}, status=402)

    KosyncCredential.objects.create(user=user, auth_key=password[:32])
    return JsonResponse({'username': username}, status=201)


@csrf_exempt
def users_auth(request):
    """GET /users/auth — validate the x-auth-user / x-auth-key pair."""
    _enabled()
    user = authenticate_kosync(request)
    if user is None:
        return _unauthorized()
    return JsonResponse({'authorized': 'OK'}, status=200)


@csrf_exempt
def progress_update(request):
    """PUT /syncs/progress — store the latest progress for a document."""
    _enabled()
    user = authenticate_kosync(request)
    if user is None:
        return _unauthorized()
    if request.method != 'PUT':
        return JsonResponse({'code': 2001, 'message': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'code': 2004, 'message': 'Invalid request'}, status=400)

    document = (data.get('document') or '').strip()
    if not _MD5_RE.match(document):
        return JsonResponse({'code': 2004, 'message': 'Invalid document'}, status=400)

    try:
        percentage = float(data.get('percentage') or 0.0)
    except (TypeError, ValueError):
        percentage = 0.0

    now = timezone.now()
    KosyncProgress.objects.update_or_create(
        user=user, document=document,
        defaults={
            'progress': str(data.get('progress') or '')[:1024],
            'percentage': percentage,
            'device': str(data.get('device') or '')[:256],
            'device_id': str(data.get('device_id') or '')[:256],
            'timestamp': now,
        },
    )
    return JsonResponse({'document': document, 'timestamp': _epoch(now)}, status=200)


@csrf_exempt
def progress_get(request, document):
    """GET /syncs/progress/:document — return the stored progress, or empty."""
    _enabled()
    user = authenticate_kosync(request)
    if user is None:
        return _unauthorized()

    row = KosyncProgress.objects.filter(user=user, document=document).first()
    if row is None:
        # KOReader tolerates an empty body (treated as "no remote progress").
        return JsonResponse({}, status=200)
    return JsonResponse({
        'document': row.document,
        'progress': row.progress,
        'percentage': row.percentage,
        'device': row.device,
        'device_id': row.device_id,
        'timestamp': _epoch(row.timestamp),
    }, status=200)


@csrf_exempt
def healthcheck(request):
    """GET /healthcheck — parity with the reference server's probe."""
    _enabled()
    return JsonResponse({'state': 'OK'}, status=200)
