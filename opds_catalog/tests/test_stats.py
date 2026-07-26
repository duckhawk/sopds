"""Download and read counters, and the "most popular" listing they enable."""
import os

import pytest
from django.urls import reverse
from constance import config

from opds_catalog import stats
from opds_catalog.models import Book, BookStat, Catalog, Counter

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FB2 = '262001.fb2'


@pytest.fixture
def library(db, django_user_model, client):
    config.SOPDS_AUTH = True
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_DOUBLES_HIDE = False
    config.SOPDS_MAXITEMS = 60
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)

    def add(title):
        return Book.objects.create(
            filename=FB2, path='.', filesize=os.path.getsize(os.path.join(DATA, FB2)),
            format='fb2', cat_type=0, docdate='2011', lang='en', title=title,
            search_title=title.upper(), annotation='', avail=2, catalog=cat)

    books = {n: add(n) for n in ('Wanted book', 'Some book', 'Ignored book')}
    client.force_login(django_user_model.objects.create_user(username='r', password='pw'))
    Counter.objects.update_known_counters()
    return books


# --- recording -------------------------------------------------------------

@pytest.mark.django_db
def test_downloading_counts(client, library):
    book = library['Wanted book']
    assert client.get(reverse('opds:download', args=[book.id, 0])).status_code == 200

    assert BookStat.objects.get(book=book).downloads == 1
    assert BookStat.objects.get(book=book).last_used is not None


@pytest.mark.django_db
def test_counts_accumulate(client, library):
    book = library['Wanted book']
    for _ in range(3):
        client.get(reverse('opds:download', args=[book.id, 0]))
    assert BookStat.objects.get(book=book).downloads == 3


@pytest.mark.django_db
def test_opening_the_reader_counts_as_a_read(client, library):
    book = library['Wanted book']
    assert client.get(reverse('web:read', args=[book.id])).status_code == 200
    assert BookStat.objects.get(book=book).reads == 1


@pytest.mark.django_db
def test_re_opening_a_cached_book_is_still_counted(client, library):
    """The content route answers a revalidation with 304 before the view runs,
    which is why the counter lives on the reader page instead."""
    book = library['Wanted book']
    for _ in range(2):
        client.get(reverse('web:read', args=[book.id]))
    assert BookStat.objects.get(book=book).reads == 2


@pytest.mark.django_db
def test_reads_and_downloads_are_counted_separately(client, library):
    book = library['Wanted book']
    client.get(reverse('opds:download', args=[book.id, 0]))
    client.get(reverse('web:read', args=[book.id]))

    row = BookStat.objects.get(book=book)
    assert (row.downloads, row.reads) == (1, 1)


@pytest.mark.django_db
def test_an_anonymous_request_records_nothing(client, library):
    """It is refused before it reaches the view."""
    client.logout()
    book = library['Wanted book']
    assert client.get(reverse('opds:download', args=[book.id, 0])).status_code == 401
    assert not BookStat.objects.exists()


@pytest.mark.django_db
def test_recording_never_breaks_the_download(client, library, monkeypatch):
    """A counter is worth less than the file the reader came for."""
    from opds_catalog.models import BookStat as Model

    def boom(*args, **kwargs):
        raise RuntimeError('stats table is on fire')

    monkeypatch.setattr(Model.objects, 'filter', boom)
    book = library['Wanted book']
    assert client.get(reverse('opds:download', args=[book.id, 0])).status_code == 200


@pytest.mark.django_db
def test_an_unknown_counter_is_a_programming_error(library):
    with pytest.raises(ValueError):
        stats.record(library['Wanted book'].id, 'sideways')


@pytest.mark.django_db
def test_deleting_a_book_takes_its_counters_with_it(library):
    book = library['Wanted book']
    stats.record(book.id, stats.DOWNLOADS)
    book.delete()
    assert not BookStat.objects.exists()


# --- reading back ----------------------------------------------------------

@pytest.mark.django_db
def test_summary_omits_untouched_books(library):
    stats.record(library['Wanted book'].id, stats.DOWNLOADS)
    got = stats.summary([b.id for b in library.values()])

    assert got[library['Wanted book'].id] == {'downloads': 1, 'reads': 0}
    assert library['Ignored book'].id not in got


@pytest.mark.django_db
def test_summary_of_nothing_is_empty(library):
    assert stats.summary([]) == {}


@pytest.mark.django_db
def test_most_popular_is_ordered_and_excludes_the_untouched(library):
    for _ in range(3):
        stats.record(library['Wanted book'].id, stats.DOWNLOADS)
    stats.record(library['Some book'].id, stats.DOWNLOADS)

    titles = [b.title for b in stats.most_popular()]
    assert titles == ['Wanted book', 'Some book']


# --- listings --------------------------------------------------------------

@pytest.mark.django_db
def test_root_feed_offers_most_popular(client, library):
    stats.record(library['Wanted book'].id, stats.DOWNLOADS)
    body = client.get(reverse('opds:main')).content.decode()
    assert 'Most popular' in body
    assert reverse('opds:popular') in body


@pytest.mark.django_db
def test_popular_feed_is_ordered(client, library):
    for _ in range(3):
        stats.record(library['Wanted book'].id, stats.DOWNLOADS)
    stats.record(library['Some book'].id, stats.DOWNLOADS)

    body = client.get(reverse('opds:popular')).content.decode()
    assert body.index('Wanted book') < body.index('Some book')
    assert 'Ignored book' not in body


@pytest.mark.django_db
def test_feed_entry_reports_the_download_count(client, library):
    stats.record(library['Wanted book'].id, stats.DOWNLOADS)
    body = client.get(reverse('opds:popular')).content.decode()
    assert 'Downloads: ' in body


@pytest.mark.django_db
def test_web_page_is_ordered_by_downloads(client, library):
    for _ in range(3):
        stats.record(library['Wanted book'].id, stats.DOWNLOADS)
    stats.record(library['Some book'].id, stats.DOWNLOADS)

    body = client.get(reverse('web:searchbooks'), {'searchtype': 'p'}).content.decode()
    assert body.index('Wanted book') < body.index('Some book')


@pytest.mark.django_db
def test_nav_links_to_most_popular(client, library):
    body = client.get(reverse('web:main')).content.decode()
    assert 'searchtype=p' in body
