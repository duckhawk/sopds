"""Prometheus metrics for the catalogue.

Everything exposed here is **derived from the database on scrape**, not
accumulated in the process. That is deliberate: the app runs under uwsgi with
several workers, so an in-process counter would only ever describe whichever
worker happened to answer the scrape, and making per-process counters correct
means `prometheus_client`'s multiprocess mode — a shared writable directory,
per-worker files and cleanup when a worker dies. Gauges read from the DB are
the same number no matter who answers, and they are the numbers worth alerting
on anyway: how big the catalogue is, how much is being taken out of it, and
how long ago the scan last finished.

Request-rate and latency histograms are genuinely per-process and are not here.
They belong behind an ingress or a sidecar that already sees every request,
rather than in a multiprocess Python app.

The exposition format is written by hand rather than pulling in
`prometheus_client`: it is a handful of gauges rendered on demand, the text
format is small and stable, and the library's real value — the process-wide
registry — is exactly the part that does not survive multiple workers.
"""
import logging

from django.core.cache import cache
from django.db import connections
from django.db.models import Sum

from constance import config

logger = logging.getLogger(__name__)

CONTENT_TYPE = 'text/plain; version=0.0.4; charset=utf-8'

# Scrapes usually land every 15s, and some of these are COUNT()s over the whole
# book table. Recompute at most this often; the numbers move on the timescale of
# a scan, not of a scrape.
CACHE_SECONDS = 30
CACHE_KEY = 'sopds-metrics'


def _escape_label(value):
    """Escape a label value per the exposition format."""
    return (str(value).replace('\\', r'\\').replace('"', r'\"').replace('\n', r'\n'))


def render(samples):
    """Render `[(name, help, type, value, labels)]` as the text format."""
    lines = []
    for name, helptext, kind, value, labels in samples:
        # A metric with no meaningful value is left out entirely — header and
        # all — rather than reported as zero: "no scan has ever finished" is not
        # "the scan finished at the epoch", and an alert on staleness has to be
        # able to tell those apart.
        if value is None:
            continue

        rendered = ''
        if labels:
            rendered = '{%s}' % ','.join('%s="%s"' % (k, _escape_label(v))
                                         for k, v in sorted(labels.items()))
        lines.append('# HELP %s %s' % (name, helptext.replace('\n', ' ')))
        lines.append('# TYPE %s %s' % (name, kind))
        lines.append('%s%s %s' % (name, rendered, value))
    return '\n'.join(lines) + '\n' if lines else ''


def collect():
    """Every sample, as the tuples `render` expects."""
    from opds_catalog import models, settings
    from opds_catalog.models import Book, BookStat, Counter, ScanRun, bookshelf

    counter = Counter.objects
    totals = BookStat.objects.aggregate(downloads=Sum('downloads'), reads=Sum('reads'))
    lastscan = counter.get_lastscan()

    return [
        ('lectern_build_info', 'Version of the running code.', 'gauge', 1,
         {'version': settings.VERSION}),

        ('lectern_books_total', 'Books in the catalogue.', 'gauge',
         counter.get_counter(models.counter_allbooks), None),
        ('lectern_authors_total', 'Authors in the catalogue.', 'gauge',
         counter.get_counter(models.counter_allauthors), None),
        ('lectern_genres_total', 'Genres in the catalogue.', 'gauge',
         counter.get_counter(models.counter_allgenres), None),
        ('lectern_series_total', 'Series in the catalogue.', 'gauge',
         counter.get_counter(models.counter_allseries), None),
        ('lectern_catalogs_total', 'Directories in the catalogue.', 'gauge',
         counter.get_counter(models.counter_allcatalogs), None),

        ('lectern_books_with_isbn_total',
         'Books whose ISBN is known, so metadata enrichment can reach them.',
         'gauge', Book.objects.exclude(isbn='').count(), None),
        ('lectern_books_enriched_total',
         'Books Open Library has already been asked about.',
         'gauge', Book.objects.filter(enriched__isnull=False).count(), None),
        ('lectern_books_rated_total', 'Books with at least one rating.', 'gauge',
         bookshelf.objects.filter(rating__isnull=False).values('book').distinct().count(),
         None),

        ('lectern_downloads_total', 'Book downloads served, all time.', 'counter',
         totals['downloads'] or 0, None),
        ('lectern_reads_total', 'Books opened in the reader, all time.', 'counter',
         totals['reads'] or 0, None),

        # The one to alert on: a scan that silently stopped running is the
        # failure mode this catalogue actually has.
        ('lectern_last_scan_timestamp_seconds',
         'When the last scan finished, in unix seconds. Absent if none ever has.',
         'gauge', int(lastscan.timestamp()) if lastscan else None, None),
    ] + _scan_samples(ScanRun)


def _scan_samples(ScanRun):
    """What the last finished scan did, and whether one is running now.

    Reported separately from the catalogue gauges because a scan that failed or
    is still going has no business changing the counts above.
    """
    last = ScanRun.objects.exclude(status=ScanRun.RUNNING).order_by('-started').first()
    running = ScanRun.objects.filter(status=ScanRun.RUNNING).count()

    samples = [
        ('lectern_scan_running', 'Scans currently in progress.', 'gauge', running, None),
    ]
    if last is None:
        return samples

    return samples + [
        ('lectern_last_scan_success',
         'Whether the last finished scan succeeded.', 'gauge',
         1 if last.status == ScanRun.OK else 0, None),
        ('lectern_last_scan_duration_seconds',
         'How long the last finished scan took.', 'gauge',
         int(last.duration_seconds or 0), None),
        ('lectern_last_scan_books_added', 'Books added by the last scan.',
         'gauge', last.books_added, None),
        ('lectern_last_scan_books_deleted', 'Books removed by the last scan.',
         'gauge', last.books_deleted, None),
        ('lectern_last_scan_bad_books',
         'Files the last scan could not parse.', 'gauge', last.bad_books, None),
        ('lectern_last_scan_bad_archives',
         'Archives the last scan could not read.', 'gauge', last.bad_archives, None),
    ]


def gather():
    """The rendered exposition text, briefly cached."""
    body = cache.get(CACHE_KEY)
    if body is None:
        body = render(collect())
        cache.set(CACHE_KEY, body, CACHE_SECONDS)
    return body


def database_up():
    try:
        connections['default'].cursor().execute('SELECT 1')
        return True
    except Exception:
        logger.warning('Metrics: database is unreachable')
        return False


def authorised(request):
    """True if this scrape may proceed.

    A token is optional — inside a cluster the endpoint is usually only
    reachable by the scraper — but when one is configured it is required, so a
    metrics endpoint that is accidentally routed to the internet does not hand
    out the shape of the library to anyone who asks.
    """
    token = (config.SOPDS_METRICS_TOKEN or '').strip()
    if not token:
        return True

    header = request.META.get('HTTP_AUTHORIZATION', '')
    scheme, _, presented = header.partition(' ')
    if scheme.lower() != 'bearer':
        return False

    # Constant-time compare so the token cannot be recovered by timing.
    import hmac
    return hmac.compare_digest(presented.strip(), token)
