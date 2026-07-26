# -*- coding: utf-8 -*-
"""A Redis cache backend that degrades instead of failing the request.

Django's `RedisCache` lets connection errors out, so with Redis unreachable
every `cache.get` in a view raises and the page 500s. The catalogue leans on the
cache in a dozen places — cover bytes, rendered EPUBs, the alphabet menu, the
stats block on every page, the metrics body, the OIDC discovery document, both
throttles — so an outage in something that exists purely to make things faster
took the whole site down with it. That was found while testing the rate limit:
the throttle handled a dead cache, and the cover view behind it did not.

Every operation here behaves, on a backend failure, exactly as it would against
an empty cache: reads miss, writes vanish. Callers already handle both, because
a cache may legitimately not have what they asked for.

Two consequences worth stating plainly rather than discovering later:

* Both throttles — the login lockout and the content rate limit — count in this
  cache, so while it is down neither is enforced. That is the same trade the
  rate limit already documents: an unenforced ceiling is a smaller problem than
  a library nobody can reach. It does mean a Redis outage is also a window with
  no brute-force protection on the login form, which is worth alerting on.
* Cover extraction and EPUB rendering stop being cached, so the server does the
  full work per request. Slow, but serving.

`lectern_cache_up` in the metrics reports the state, so the degradation is
visible rather than merely survivable.
"""
import logging

from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger(__name__)


def _redis_errors():
    """The exception types that mean "the cache is not answering".

    Imported lazily and defensively: redis is only installed where it is used,
    and the backend must not fail to import without it.
    """
    try:
        from redis import exceptions
        return (exceptions.RedisError, OSError)
    except Exception:      # pragma: no cover - redis is a declared dependency
        return (OSError,)


class ResilientRedisCache(RedisCache):
    """`RedisCache`, but an unreachable Redis is a miss rather than a 500."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._errors = _redis_errors()

    def _degrade(self, operation, err):
        # warning, not exception: an outage produces one of these per request,
        # and a stack trace on each would bury everything else in the log.
        logger.warning('Cache unavailable, %s degraded: %s', operation, err)

    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default, version)
        except self._errors as err:
            self._degrade('get', err)
            return default

    def get_many(self, keys, version=None):
        try:
            return super().get_many(keys, version)
        except self._errors as err:
            self._degrade('get_many', err)
            return {}

    def set(self, key, value, timeout=None, version=None, **kwargs):
        try:
            return super().set(key, value, timeout, version, **kwargs)
        except self._errors as err:
            self._degrade('set', err)
            return False

    def set_many(self, data, timeout=None, version=None):
        try:
            return super().set_many(data, timeout, version)
        except self._errors as err:
            self._degrade('set_many', err)
            return list(data)          # "none of these were stored"

    def add(self, key, value, timeout=None, version=None):
        try:
            return super().add(key, value, timeout, version)
        except self._errors as err:
            self._degrade('add', err)
            return False

    def touch(self, key, timeout=None, version=None):
        try:
            return super().touch(key, timeout, version)
        except self._errors as err:
            self._degrade('touch', err)
            return False

    def delete(self, key, version=None):
        try:
            return super().delete(key, version)
        except self._errors as err:
            self._degrade('delete', err)
            return False

    def delete_many(self, keys, version=None):
        try:
            return super().delete_many(keys, version)
        except self._errors as err:
            self._degrade('delete_many', err)

    def has_key(self, key, version=None):
        try:
            return super().has_key(key, version)
        except self._errors as err:
            self._degrade('has_key', err)
            return False

    def incr(self, key, delta=1, version=None):
        """Raise ValueError on an outage, which is what "no such key" raises.

        Both counters that use `incr` already handle that — it is how they seed
        a new window — so a dead cache makes every request look like the first
        one of its window, and nothing accumulates. Deliberate: see the module
        docstring.
        """
        try:
            return super().incr(key, delta, version)
        except self._errors as err:
            self._degrade('incr', err)
            raise ValueError('cache unavailable') from err

    def clear(self):
        try:
            return super().clear()
        except self._errors as err:
            self._degrade('clear', err)


def is_up():
    """Whether the shared cache is answering right now.

    Deliberately does not use the resilient wrapper's swallowing: this is the
    one caller that wants the truth rather than a graceful default.
    """
    from django.core.cache import DEFAULT_CACHE_ALIAS, caches

    # `caches[...]`, not `django.core.cache.cache`: that name is a proxy object,
    # so an isinstance check against it is always False and this would report a
    # dead cache as healthy.
    backend = caches[DEFAULT_CACHE_ALIAS]

    probe = 'sopds-cache-probe'
    try:
        # A round trip, not just a connect: a Redis that accepts connections and
        # then refuses writes (out of memory, a read-only replica) is not up for
        # our purposes.
        if isinstance(backend, ResilientRedisCache):
            # Straight to the parent, bypassing the swallowing above — this is
            # the one caller that wants the error rather than a graceful miss.
            RedisCache.set(backend, probe, 1, 10)
        else:
            backend.set(probe, 1, 10)
        return True
    except Exception:
        return False
