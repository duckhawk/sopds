"""Liveness/readiness probes for k8s, and the Prometheus scrape endpoint."""
from django.db import connections
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse

from constance import config

from sopds import metrics


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


def metrics_view(request):
    """GET /metrics — Prometheus exposition.

    Off by default: it describes the size and use of the library, which is not
    something to start publishing without being asked. `SOPDS_METRICS_ENABLE`
    turns it on and `SOPDS_METRICS_TOKEN` optionally locks it to one scraper.
    """
    if not config.SOPDS_METRICS_ENABLE:
        return HttpResponse('metrics are disabled', status=404, content_type='text/plain')
    if not metrics.authorised(request):
        return HttpResponseForbidden('forbidden')

    up = metrics.database_up()
    body = metrics.gather() if up else ''
    body += metrics.render([
        ('lectern_database_up', 'Whether the catalogue database answers.', 'gauge',
         1 if up else 0, None),
        # A degraded cache does not stop the catalogue serving, so nothing else
        # reports it; without this it is invisible until someone notices the
        # latency. It is also a window with no brute-force protection on login.
        ('lectern_cache_up', 'Whether the shared cache answers.', 'gauge',
         1 if metrics.cache_up() else 0, None),
    ])
    return HttpResponse(body, content_type=metrics.CONTENT_TYPE)
