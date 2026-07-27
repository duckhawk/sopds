# -*- coding: utf-8 -*-
"""Sending requests for a superseded hostname to the current one.

A library that has been running for a while is saved in places nobody can
edit: an OPDS catalogue entry on an e-reader, a bookmark, a sync configuration
inside KOReader. Renaming the site breaks all of them at once unless the old
name keeps answering, so the old host stays in `ALLOWED_HOSTS` and every
request to it is answered with a permanent redirect to the new one.

Done here rather than in the ingress on purpose. The obvious nginx-ingress
annotation, `permanent-redirect`, emits a literal target and drops the path —
which would send an e-reader asking for `/opds/search/...` to the front page.
Getting the path across needs a configuration snippet, and snippets are
disabled by default in current ingress-nginx. This is four lines, it keeps the
path and the query string, and it can be tested.
"""
from django.http import HttpResponsePermanentRedirect
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

# Probes address the pod by whatever host they were configured with, and a
# redirect would read as a failure. They are also not URLs anyone bookmarks.
EXEMPT = ('/healthz', '/readyz', '/metrics')


class CanonicalHostMiddleware(MiddlewareMixin):
    """301 to `settings.CANONICAL_HOST` when the request arrived elsewhere.

    Inert unless CANONICAL_HOST is set, which is how every installation that
    has only ever had one name is left alone.
    """

    def process_request(self, request):
        canonical = getattr(settings, 'CANONICAL_HOST', '')
        if not canonical:
            return None

        host = request.get_host()
        if host == canonical or host.split(':')[0] == canonical:
            return None

        if request.path.startswith(EXEMPT):
            return None

        return HttpResponsePermanentRedirect(
            '%s://%s%s' % (request.scheme, canonical, request.get_full_path()))
