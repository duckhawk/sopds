"""Attributing kosync progress to a catalogue Book, and onto the user's shelf."""
import hashlib
import json
import os

import pytest
from django.core.management import call_command
from constance import config

from opds_catalog.models import Book, Catalog, bookshelf
from sopds_sync import linking
from sopds_sync.digest import filename_md5, partial_md5
from sopds_sync.models import BookDigest, KosyncCredential, KosyncProgress

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'opds_catalog', 'tests', 'data')
FB2 = '262001.fb2'


def md5(s):
    return hashlib.md5(s.encode('utf-8')).hexdigest()


def hdr(username='reader', password='syncpass'):
    return {'HTTP_X_AUTH_USER': username, 'HTTP_X_AUTH_KEY': md5(password)}


@pytest.fixture(autouse=True)
def enable_kosync(db):
    config.SOPDS_KOSYNC_ENABLE = True
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_TITLE_AS_FILENAME = True


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username='reader', password='pw')


@pytest.fixture
def cred(user):
    c = KosyncCredential(user=user)
    c.set_password('syncpass')
    c.save()
    return c


@pytest.fixture
def book(db):
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    return Book.objects.create(
        filename=FB2, path='.', filesize=os.path.getsize(os.path.join(DATA, FB2)),
        format='fb2', cat_type=0, docdate='2011', lang='en',
        title='The Sanctuary Sparrow', search_title='THE SANCTUARY SPARROW',
        annotation='', avail=2, catalog=cat,
    )


def put_progress(client, document, percentage, **extra):
    return client.put(
        '/kosync/syncs/progress',
        data=json.dumps({'document': document, 'progress': 'xp', 'percentage': percentage}),
        content_type='application/json', **extra)


# --- indexing --------------------------------------------------------------

@pytest.mark.django_db
def test_index_records_a_content_digest_and_the_name_digests(book):
    call_command('sopds_kosync_index')

    digests = {d.digest: d.method for d in BookDigest.objects.filter(book=book)}
    with open(os.path.join(DATA, FB2), 'rb') as f:
        assert digests[partial_md5(f)] == BookDigest.BINARY
    # The name the file already has, and the transliterated-title name an OPDS
    # download hands out.
    assert digests[filename_md5(FB2)] == BookDigest.FILENAME
    assert filename_md5('The_Sanctuary_Sparrow.fb2') in digests


@pytest.mark.django_db
def test_index_is_idempotent(book):
    call_command('sopds_kosync_index')
    before = BookDigest.objects.count()
    call_command('sopds_kosync_index')
    assert BookDigest.objects.count() == before


@pytest.mark.django_db
def test_skip_binary_indexes_names_only(book):
    call_command('sopds_kosync_index', '--skip-binary')
    assert not BookDigest.objects.filter(method=BookDigest.BINARY).exists()
    assert BookDigest.objects.filter(method=BookDigest.FILENAME).exists()


@pytest.mark.django_db
def test_dry_run_writes_nothing(book):
    call_command('sopds_kosync_index', '--dry-run')
    assert BookDigest.objects.count() == 0


@pytest.mark.django_db
def test_a_missing_file_does_not_stop_the_run(book, db):
    cat = Catalog.objects.get()
    Book.objects.create(filename='gone.fb2', path='.', filesize=1, format='fb2',
                        cat_type=0, docdate='2011', lang='en', title='Gone',
                        search_title='GONE', annotation='', avail=2, catalog=cat)
    call_command('sopds_kosync_index')
    # The readable book is still indexed.
    assert BookDigest.objects.filter(book=book, method=BookDigest.BINARY).exists()


@pytest.mark.django_db
def test_two_books_cannot_claim_the_same_digest(book, db):
    """A duplicate file hashes identically; the first indexed keeps the hash
    rather than the pair of them fighting over it."""
    cat = Catalog.objects.get()
    Book.objects.create(filename=FB2, path='.', filesize=book.filesize, format='fb2',
                        cat_type=0, docdate='2011', lang='en',
                        title='The Sanctuary Sparrow', search_title='THE SANCTUARY SPARROW',
                        annotation='', avail=2, catalog=cat)
    call_command('sopds_kosync_index')

    with open(os.path.join(DATA, FB2), 'rb') as f:
        digest = partial_md5(f)
    assert BookDigest.objects.filter(digest=digest).count() == 1


# --- resolution ------------------------------------------------------------

@pytest.mark.django_db
def test_resolve_book_finds_an_indexed_digest(book):
    call_command('sopds_kosync_index')
    assert linking.resolve_book(filename_md5(FB2)) == book


@pytest.mark.django_db
def test_resolve_book_returns_none_for_an_unknown_document(book):
    call_command('sopds_kosync_index')
    assert linking.resolve_book('0' * 32) is None
    assert linking.resolve_book('') is None


# --- progress lands on the shelf -------------------------------------------

@pytest.mark.django_db
def test_progress_from_a_reader_names_the_book_and_marks_it_reading(client, cred, book, user):
    call_command('sopds_kosync_index')

    resp = put_progress(client, filename_md5(FB2), 0.42, **hdr())
    assert resp.status_code == 200

    assert KosyncProgress.objects.get(user=user).book == book
    shelf = bookshelf.objects.get(user=user, book=book)
    assert shelf.status == bookshelf.STATUS_READING
    assert shelf.percent == pytest.approx(0.42)


@pytest.mark.django_db
def test_finishing_a_book_marks_it_read(client, cred, book, user):
    call_command('sopds_kosync_index')
    put_progress(client, filename_md5(FB2), 1.0, **hdr())
    assert bookshelf.objects.get(user=user, book=book).status == bookshelf.STATUS_READ


@pytest.mark.django_db
def test_progress_never_goes_backwards(client, cred, book, user):
    """A second device syncing a stale position must not undo the first."""
    call_command('sopds_kosync_index')
    put_progress(client, filename_md5(FB2), 0.80, **hdr())
    put_progress(client, filename_md5(FB2), 0.10, **hdr())

    shelf = bookshelf.objects.get(user=user, book=book)
    assert shelf.percent == pytest.approx(0.80)
    assert shelf.status == bookshelf.STATUS_READING


@pytest.mark.django_db
def test_a_finished_book_is_not_demoted_to_reading(client, cred, book, user):
    call_command('sopds_kosync_index')
    put_progress(client, filename_md5(FB2), 1.0, **hdr())
    put_progress(client, filename_md5(FB2), 0.05, **hdr())
    assert bookshelf.objects.get(user=user, book=book).status == bookshelf.STATUS_READ


@pytest.mark.django_db
def test_an_unknown_document_still_syncs(client, cred, book, user):
    """A book the reader did not get from this catalogue: kosync is a key-value
    store first, and must keep working without a Book to point at."""
    resp = put_progress(client, 'f' * 32, 0.5, **hdr())
    assert resp.status_code == 200

    row = KosyncProgress.objects.get(user=user)
    assert row.book is None
    assert row.percentage == pytest.approx(0.5)
    assert not bookshelf.objects.exists()

    got = client.get('/kosync/syncs/progress/%s' % ('f' * 32), **hdr())
    assert got.json()['percentage'] == pytest.approx(0.5)


@pytest.mark.django_db
def test_a_shelf_failure_cannot_break_the_sync(client, cred, book, user, monkeypatch):
    """A reader that got an error here would retry forever and lose the position
    it was trying to save."""
    call_command('sopds_kosync_index')

    def boom(*args, **kwargs):
        raise RuntimeError('shelf is on fire')

    monkeypatch.setattr(linking, 'record_progress', boom)

    resp = put_progress(client, filename_md5(FB2), 0.42, **hdr())
    assert resp.status_code == 200
    assert KosyncProgress.objects.get(user=user).percentage == pytest.approx(0.42)


@pytest.mark.django_db
def test_deleting_a_book_leaves_the_progress_row(book, user):
    """Progress is the user's, not the catalogue's: a rescan that drops a book
    must not delete what the reader synced."""
    call_command('sopds_kosync_index')
    KosyncProgress.objects.create(user=user, document=filename_md5(FB2), book=book,
                                  progress='xp', percentage=0.5)
    book.delete()

    row = KosyncProgress.objects.get(user=user)
    assert row.book is None
    assert row.percentage == pytest.approx(0.5)


# --- the web UI sees it ----------------------------------------------------

@pytest.mark.django_db
def test_the_book_list_shows_the_reader_progress(client, cred, book, user):
    config.SOPDS_AUTH = True
    call_command('sopds_kosync_index')
    put_progress(client, filename_md5(FB2), 0.42, **hdr())

    client.force_login(user)
    body = client.get('/web/search/books/', {'searchtype': 'm', 'searchterms': 'SANCTUARY'}).content.decode()
    assert '42%' in body
