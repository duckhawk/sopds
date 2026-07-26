"""What a library scan leaves behind.

The scanner counted all of this already and then only wrote it to a log file,
so the outcome of a scan was invisible to the application — including whether
it finished at all.
"""
import os

import pytest
from django.core.cache import cache
from constance import config

from opds_catalog import opdsdb
from opds_catalog.models import ScanRun
from opds_catalog.sopdscan import opdsScanner
from sopds import metrics

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


@pytest.fixture
def library(db):
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_INPX_ENABLE = False
    opdsdb.clear_all()
    return DATA


@pytest.mark.django_db
def test_a_scan_records_a_run(library):
    opdsScanner().scan_all()

    run = ScanRun.objects.get()
    assert run.status == ScanRun.OK
    assert run.finished is not None
    assert run.duration_seconds is not None


@pytest.mark.django_db
def test_the_run_carries_the_counters_the_scan_logged(library):
    scanner = opdsScanner()
    scanner.scan_all()

    run = ScanRun.objects.get()
    assert run.books_added == scanner.books_added
    assert run.books_added > 0
    assert run.bad_books == scanner.bad_books
    assert run.arch_scanned == scanner.arch_scanned


@pytest.mark.django_db
def test_each_scan_adds_a_row(library):
    opdsScanner().scan_all()
    opdsScanner().scan_all()
    assert ScanRun.objects.count() == 2


@pytest.mark.django_db
def test_a_second_scan_adds_nothing_and_says_so(library):
    opdsScanner().scan_all()
    opdsScanner().scan_all()

    latest = ScanRun.objects.order_by('-started').first()
    assert latest.books_added == 0
    assert latest.status == ScanRun.OK


@pytest.mark.django_db
def test_a_failed_scan_is_recorded_rather_than_left_running(library):
    """A scan that dies used to leave nothing but a stack trace in a log file
    and a catalogue that silently stopped growing."""
    scanner = opdsScanner()
    scanner.run = scanner.start_report()
    scanner.finish_report(ScanRun.FAILED, error='OSError: disk went away')

    run = ScanRun.objects.get()
    assert run.status == ScanRun.FAILED
    assert 'disk went away' in run.error
    assert run.finished is not None


@pytest.mark.django_db
def test_reporting_never_stops_a_scan(library, monkeypatch):
    """Observability is not worth failing the thing being observed."""
    def boom(*args, **kwargs):
        raise RuntimeError('reporting table is gone')

    monkeypatch.setattr(ScanRun.objects, 'create', boom)
    scanner = opdsScanner()
    scanner.scan_all()          # must not raise

    assert scanner.books_added > 0
    assert not ScanRun.objects.exists()


@pytest.mark.django_db
def test_an_overlong_error_is_truncated_not_rejected(library):
    scanner = opdsScanner()
    scanner.run = scanner.start_report()
    scanner.finish_report(ScanRun.FAILED, error='x' * 10000)
    assert len(ScanRun.objects.get().error) == 4000


# --- what the metrics say about it -----------------------------------------

@pytest.fixture
def scraping(db):
    config.SOPDS_METRICS_ENABLE = True
    config.SOPDS_METRICS_TOKEN = ''
    cache.delete(metrics.CACHE_KEY)
    yield
    cache.delete(metrics.CACHE_KEY)


def values(body):
    out = {}
    for line in body.splitlines():
        if line and not line.startswith('#'):
            name, _, value = line.rpartition(' ')
            out[name.split('{')[0]] = value
    return out


@pytest.mark.django_db
def test_metrics_report_the_last_scan(client, library, scraping):
    opdsScanner().scan_all()
    cache.delete(metrics.CACHE_KEY)

    got = values(client.get('/metrics').content.decode())
    assert got['lectern_last_scan_success'] == '1'
    assert got['lectern_scan_running'] == '0'
    assert int(got['lectern_last_scan_books_added']) > 0
    assert 'lectern_last_scan_duration_seconds' in got


@pytest.mark.django_db
def test_metrics_report_a_failure(client, library, scraping):
    scanner = opdsScanner()
    scanner.run = scanner.start_report()
    scanner.finish_report(ScanRun.FAILED, error='boom')
    cache.delete(metrics.CACHE_KEY)

    got = values(client.get('/metrics').content.decode())
    assert got['lectern_last_scan_success'] == '0'


@pytest.mark.django_db
def test_metrics_show_a_scan_in_progress(client, library, scraping):
    ScanRun.objects.create(status=ScanRun.RUNNING)
    cache.delete(metrics.CACHE_KEY)

    got = values(client.get('/metrics').content.decode())
    assert got['lectern_scan_running'] == '1'
    # A run still going must not be reported as the last outcome.
    assert 'lectern_last_scan_success' not in got


@pytest.mark.django_db
def test_metrics_before_any_scan_omit_the_scan_outcome(client, db, scraping):
    got = values(client.get('/metrics').content.decode())
    assert got['lectern_scan_running'] == '0'
    assert 'lectern_last_scan_success' not in got
