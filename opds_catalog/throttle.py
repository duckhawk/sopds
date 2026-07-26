# -*- coding: utf-8 -*-
"""A rate limit for the routes that cost something to serve.

The login form has been throttled for a while; nothing else was. That mattered
less when every content route was a file read, and matters more now that one of
them unzips a book and parses every document in its spine. The cache in front
of that helps a warm book and does nothing for a client walking the catalogue.

This is not access control — the reader is already authenticated by the time it
gets here. It is a ceiling on how fast one client can ask, so a runaway sync
loop or a mirroring script degrades itself instead of the server.

The counter lives in the shared cache, like the login throttle, so the limit is
the same limit across uwsgi workers rather than one per worker.
"""
import logging

from django.core.cache import cache
from django.http import HttpResponse

from constance import config

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


def client_id(request):
    """Who to count against.

    A signed-in reader is counted as themselves rather than as their address:
    a household behind one NAT is several readers, and one of them syncing a
    library should not lock out the rest.
    """
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return 'u%s' % user.pk

    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return 'i%s' % forwarded.split(',')[0].strip()
    return 'i%s' % request.META.get('REMOTE_ADDR', '')


def over_limit(request):
    """True when this client has already used up the current minute."""
    limit = config.SOPDS_RATE_LIMIT
    if not limit or limit <= 0:
        return False

    key = 'sopds-throttle:%s' % client_id(request)
    try:
        # incr on a missing key raises rather than starting at zero, which is
        # also how we learn to seed it with the window's expiry attached.
        used = cache.incr(key)
    except ValueError:
        cache.set(key, 1, WINDOW_SECONDS)
        used = 1
    except Exception:
        # A cache that is down must not take the catalogue down with it: an
        # unenforced limit is a much smaller problem than a refused library.
        logger.warning('Rate limiting is disabled: the cache is unavailable')
        return False

    if used == limit + 1:
        # Once per client per window, not once per rejected request — the whole
        # point is that there are about to be a great many of these.
        logger.warning('Rate limit reached by %s (%d/min)', client_id(request), limit)
    return used > limit


def too_many(request):
    response = HttpResponse('rate limit exceeded', status=429, content_type='text/plain')
    response['Retry-After'] = str(WINDOW_SECONDS)
    return response
