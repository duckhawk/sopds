"""A correlation id on every request, in the logs and back to the caller."""
import logging

import pytest
from django.urls import reverse

from sopds import request_id
from sopds.request_id import HEADER, RequestIDFilter, RequestIDMiddleware, sanitize


# --- the header ------------------------------------------------------------

@pytest.mark.django_db
def test_every_response_carries_an_id(client):
    resp = client.get('/healthz')
    assert resp[HEADER]
    assert len(resp[HEADER]) >= 8


@pytest.mark.django_db
def test_two_requests_get_different_ids(client):
    first = client.get('/healthz')[HEADER]
    second = client.get('/healthz')[HEADER]
    assert first != second


@pytest.mark.django_db
def test_an_upstream_id_is_kept(client):
    """An ingress that stamps requests should see the same value end to end."""
    resp = client.get('/healthz', headers={'x-request-id': 'edge-abc123'})
    assert resp[HEADER] == 'edge-abc123'


@pytest.mark.django_db
def test_an_id_survives_an_error_response(client):
    """The responses worth correlating are exactly the ones that went wrong."""
    resp = client.get(reverse('opds:cover', args=[999999]))
    assert resp.status_code in (401, 404)
    assert resp[HEADER]


# --- untrusted input -------------------------------------------------------

@pytest.mark.parametrize('given, expected', [
    ('plain-123', 'plain-123'),
    ('  spaced  ', 'spaced'),
    ('a b c', 'abc'),
    ('drop\nnewline', 'dropnewline'),
    ('semi;colon', 'semicolon'),
    ('', None),
    ('   ', None),
    (None, None),
    ('!!!', None),
])
def test_a_caller_supplied_id_is_cleaned(given, expected):
    assert sanitize(given) == expected


def test_an_overlong_id_is_truncated():
    assert len(sanitize('x' * 500)) == 64


@pytest.mark.django_db
def test_a_newline_in_the_header_cannot_forge_a_log_line(client):
    """It ends up in log records; a newline there turns one record into two."""
    resp = client.get('/healthz', headers={'x-request-id': 'good\nINFO fake line'})
    assert '\n' not in resp[HEADER]
    assert resp[HEADER] == 'goodINFOfakeline'


@pytest.mark.django_db
def test_an_unusable_id_falls_back_to_a_generated_one(client):
    resp = client.get('/healthz', headers={'x-request-id': '!!!!'})
    assert resp[HEADER] and resp[HEADER] != '!!!!'


# --- logging ---------------------------------------------------------------

def test_the_filter_tags_records_outside_a_request():
    record = logging.LogRecord('n', logging.INFO, __file__, 1, 'msg', (), None)
    assert RequestIDFilter().filter(record) is True
    assert record.request_id == '-'


def test_log_lines_emitted_during_a_request_carry_its_id():
    """The whole point: a line in the log can be traced back to one request.

    Asserted on the formatted output, because the tag is applied by the filter
    at the moment the record is handled — inspecting the record afterwards, once
    the request has finished, only ever shows the out-of-request placeholder.
    """
    import io
    from django.http import HttpResponse
    from django.test import RequestFactory

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RequestIDFilter())
    handler.setFormatter(logging.Formatter('[%(request_id)s] %(message)s'))

    log = logging.getLogger('opds_catalog.test_request_id')
    log.addHandler(handler)
    log.propagate = False
    try:
        def view(request):
            log.warning('something happened')
            return HttpResponse('ok')

        response = RequestIDMiddleware(view)(RequestFactory().get('/'))
        log.warning('after the request')
    finally:
        log.removeHandler(handler)

    during, after = stream.getvalue().strip().splitlines()
    assert during == '[%s] something happened' % response[HEADER]
    assert after == '[-] after the request'


def test_the_id_is_cleared_after_the_request():
    """Otherwise a worker's next request inherits the previous one's id."""
    from django.http import HttpResponse
    from django.test import RequestFactory

    middleware = RequestIDMiddleware(lambda r: HttpResponse('ok'))
    middleware(RequestFactory().get('/'))
    assert request_id.get() == '-'


def test_the_id_is_cleared_even_when_the_view_raises(monkeypatch):
    from django.test import RequestFactory

    def boom(request):
        raise RuntimeError('view exploded')

    middleware = RequestIDMiddleware(boom)
    with pytest.raises(RuntimeError):
        middleware(RequestFactory().get('/'))
    assert request_id.get() == '-'
