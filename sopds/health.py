"""Liveness/readiness probes for k8s. Unauthenticated and cheap."""
from django.db import connections
from django.http import HttpResponse, JsonResponse


def healthz(request):
    """Liveness: the process is up. Does not touch the DB."""
    return HttpResponse('ok', content_type='text/plain')


def readyz(request):
    """Readiness: the app can serve requests (DB reachable)."""
    try:
        connections['default'].cursor().execute('SELECT 1')
    except Exception:
        return JsonResponse({'status': 'db-unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})
