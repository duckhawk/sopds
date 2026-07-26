"""The Prometheus scrape endpoint."""
import pytest
from django.core.cache import cache
from django.utils import timezone
from constance import config

from opds_catalog import stats
from opds_catalog.models import Book, BookStat, Catalog, Counter, bookshelf
from sopds import metrics


@pytest.fixture(autouse=True)
def enabled(db):
    config.SOPDS_METRICS_ENABLE = True
    config.SOPDS_METRICS_TOKEN = ''
    cache.delete(metrics.CACHE_KEY)
    yield
    cache.delete(metrics.CACHE_KEY)


@pytest.fixture
def catalogue(db, django_user_model):
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    books = []
    for n, isbn in enumerate(['9780306406157', '']):
        books.append(Book.objects.create(
            filename='b%d.fb2' % n, path='.', filesize=1, format='fb2', cat_type=0,
            docdate='2011', lang='en', title='B%d' % n, search_title='B%d' % n,
            annotation='', avail=2, catalog=cat, isbn=isbn))
    user = django_user_model.objects.create_user(username='r', password='pw')
    bookshelf.objects.create(user=user, book=books[0], rating=4)
    Counter.objects.update_known_counters()
    return books


def scrape(client, **extra):
    return client.get('/metrics', **extra)


def values(body):
    """{metric name (with labels stripped): value} from an exposition body."""
    out = {}
    for line in body.splitlines():
        if not line or line.startswith('#'):
            continue
        name, _, value = line.rpartition(' ')
        out[name.split('{')[0]] = value
    return out


# --- shape -----------------------------------------------------------------

@pytest.mark.django_db
def test_exposes_the_prometheus_content_type(client, catalogue):
    resp = scrape(client)
    assert resp.status_code == 200
    assert resp['Content-Type'].startswith('text/plain')
    assert 'version=0.0.4' in resp['Content-Type']


@pytest.mark.django_db
def test_every_metric_has_help_and_type(client, catalogue):
    body = scrape(client).content.decode()
    named = {ln.split()[2] for ln in body.splitlines() if ln.startswith('# HELP')}
    typed = {ln.split()[2] for ln in body.splitlines() if ln.startswith('# TYPE')}
    assert named and named == typed

    for name in values(body):
        assert name in named, '%s has no HELP line' % name


@pytest.mark.django_db
def test_counts_come_from_the_catalogue(client, catalogue):
    got = values(scrape(client).content.decode())
    assert got['lectern_books_total'] == '2'
    assert got['lectern_books_with_isbn_total'] == '1'
    assert got['lectern_books_rated_total'] == '1'
    assert got['lectern_database_up'] == '1'


@pytest.mark.django_db
def test_build_info_carries_the_version(client, catalogue):
    from opds_catalog import settings as catalog_settings
    body = scrape(client).content.decode()
    assert 'lectern_build_info{version="%s"} 1' % catalog_settings.VERSION in body


@pytest.mark.django_db
def test_download_and_read_totals(client, catalogue):
    stats.record(catalogue[0].id, stats.DOWNLOADS)
    stats.record(catalogue[0].id, stats.DOWNLOADS)
    stats.record(catalogue[1].id, stats.READS)
    cache.delete(metrics.CACHE_KEY)

    got = values(scrape(client).content.decode())
    assert got['lectern_downloads_total'] == '2'
    assert got['lectern_reads_total'] == '1'


@pytest.mark.django_db
def test_totals_are_zero_not_missing_when_nothing_was_taken(client, catalogue):
    assert BookStat.objects.count() == 0
    got = values(scrape(client).content.decode())
    assert got['lectern_downloads_total'] == '0'


# --- the metric worth alerting on ------------------------------------------

@pytest.mark.django_db
def test_last_scan_is_reported_as_a_unix_timestamp(client, catalogue):
    got = values(scrape(client).content.decode())
    when = int(got['lectern_last_scan_timestamp_seconds'])
    assert abs(when - int(timezone.now().timestamp())) < 300


@pytest.mark.django_db
def test_last_scan_is_absent_rather_than_zero_when_none_has_run(client, db):
    """"Never scanned" is not "scanned at the epoch" — an alert on staleness
    has to be able to tell those apart."""
    Counter.objects.all().delete()
    cache.delete(metrics.CACHE_KEY)
    body = scrape(client).content.decode()
    assert 'lectern_last_scan_timestamp_seconds' not in values(body)
    assert 'lectern_last_scan_timestamp_seconds' not in body   # not even a header


# --- access ----------------------------------------------------------------

@pytest.mark.django_db
def test_disabled_by_default_is_a_404(client, catalogue):
    config.SOPDS_METRICS_ENABLE = False
    assert scrape(client).status_code == 404


@pytest.mark.django_db
def test_a_configured_token_is_required(client, catalogue):
    config.SOPDS_METRICS_TOKEN = 'sekrit'
    assert scrape(client).status_code == 403
    assert scrape(client, HTTP_AUTHORIZATION='Bearer wrong').status_code == 403
    assert scrape(client, HTTP_AUTHORIZATION='Basic sekrit').status_code == 403
    assert scrape(client, HTTP_AUTHORIZATION='Bearer sekrit').status_code == 200


@pytest.mark.django_db
def test_no_token_configured_means_no_token_needed(client, catalogue):
    assert scrape(client).status_code == 200


@pytest.mark.django_db
def test_scraping_does_not_require_a_catalogue_login(client, catalogue):
    """Prometheus cannot log in; SOPDS_AUTH must not gate this."""
    config.SOPDS_AUTH = True
    assert scrape(client).status_code == 200


# --- cost ------------------------------------------------------------------

@pytest.mark.django_db
def test_the_body_is_cached_between_scrapes(client, catalogue, monkeypatch):
    """Some of these are COUNT()s over the whole book table, and scrapes are
    frequent."""
    first = scrape(client).content.decode()

    def fail():
        raise AssertionError('metrics were recomputed on every scrape')

    monkeypatch.setattr(metrics, 'collect', fail)
    assert scrape(client).content.decode() == first


# --- rendering -------------------------------------------------------------

def test_label_values_are_escaped():
    body = metrics.render([('m', 'h', 'gauge', 1, {'l': 'a"b\\c'})])
    assert 'm{l="a\\"b\\\\c"} 1' in body


def test_a_none_value_is_omitted_entirely():
    """Header and all, so the family does not show up as an empty one."""
    assert metrics.render([('m', 'h', 'gauge', None, None)]) == ''
    # A present neighbour is still rendered.
    body = metrics.render([('gone', 'h', 'gauge', None, None),
                           ('kept', 'h', 'gauge', 7, None)])
    assert 'gone' not in body
    assert values(body) == {'kept': '7'}


def test_help_text_stays_on_one_line():
    body = metrics.render([('m', 'two\nlines', 'gauge', 1, None)])
    assert '# HELP m two lines' in body
