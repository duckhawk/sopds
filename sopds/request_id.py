# -*- coding: utf-8 -*-
"""A correlation id for every request, in the logs and back to the caller.

Without one, a report of "a download failed a few minutes ago" has to be matched
against the log by guesswork — several uwsgi workers interleave their lines, and
an e-reader polling feeds produces a lot of them. With one, the id in the
response header names exactly the lines to look at.

The id is taken from `X-Request-ID` when something upstream already set one, so
an ingress that stamps requests keeps the same value end to end, and generated
otherwise. An inbound value is length-limited and stripped of anything unusual:
it is attacker-supplied and ends up in log lines, and a newline in a log line is
how one record becomes two.
"""
import logging
import re
import uuid
from contextvars import ContextVar

HEADER = 'X-Request-ID'
META_KEY = 'HTTP_X_REQUEST_ID'

MAX_LENGTH = 64
SAFE = re.compile(r'[^A-Za-z0-9._:-]')

# A ContextVar rather than thread-local storage: it is what Django's own async
# support uses, so this keeps working if a view is ever handled off the request
# thread.
_current = ContextVar('request_id', default='-')


def get():
    """The current request's id, or '-' outside a request."""
    return _current.get()


def sanitize(value):
    """A caller-supplied id, made safe to put in a log line."""
    cleaned = SAFE.sub('', (value or '').strip())[:MAX_LENGTH]
    return cleaned or None


class RequestIDMiddleware:
    """Assign an id to the request, echo it, and expose it to logging."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = sanitize(request.META.get(META_KEY)) or uuid.uuid4().hex
        request.request_id = request_id
        token = _current.set(request_id)

        # Tag the Sentry scope too, so an event and a log line can be lined up
        # from either direction. Sentry is optional, and never worth an error.
        try:
            import sentry_sdk
            sentry_sdk.set_tag('request_id', request_id)
        except Exception:
            pass

        try:
            response = self.get_response(request)
        finally:
            _current.reset(token)

        response[HEADER] = request_id
        return response


class RequestIDFilter(logging.Filter):
    """Put `request_id` on every record so a formatter can print it.

    A filter rather than an adapter because it has to reach records emitted by
    Django and by libraries, which know nothing about this.
    """

    def filter(self, record):
        record.request_id = get()
        return True
