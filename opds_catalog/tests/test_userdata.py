"""Exporting and restoring the part of the database a rescan cannot rebuild.

The point of the whole thing is the round trip through a *rebuilt* catalogue,
where every book has a different id — which is exactly what `dumpdata` cannot
survive.
"""
import io
import json
import os

import pytest
from django.core.management import call_command
from django.utils import timezone

from opds_catalog.models import Book, BookStat, Catalog, Theme, bookshelf
from sopds import userdata
from sopds_sync.models import BookDigest, KosyncProgress

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


@pytest.fixture
def populated(db, django_user_model):
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)

    def add(name):
        return Book.objects.create(
            filename=name, path='shelf', filesize=1, format='fb2', cat_type=0,
            docdate='', lang='en', title=name, search_title=name.upper(),
            annotation='', avail=2, catalog=cat)

    first, second = add('one.fb2'), add('two.fb2')
    first.isbn = '9780306406157'
    first.publisher = 'Gollancz'
    first.annotation = 'From Open Library.'
    first.enriched = timezone.now()
    first.save()

    reader = django_user_model.objects.create_user(username='reader', password='pw')
    bookshelf.objects.create(user=reader, book=first, status=bookshelf.STATUS_READING,
                             rating=4, percent=0.42, position='2.13')
    Theme.objects.create(user=reader, theme_css='css/sopds-dark.css', font_size=120)
    BookStat.objects.create(book=first, downloads=7, reads=3)
    KosyncProgress.objects.create(user=reader, document='d' * 32,
                                  progress='xpointer', percentage=0.5)
    return {'cat': cat, 'first': first, 'second': second, 'reader': reader}


def rebuild(catalog, names=('one.fb2', 'two.fb2'), path='shelf'):
    """Throw the catalogue away and rescan it, so every book gets a new id."""
    Book.objects.all().delete()
    made = []
    for name in names:
        made.append(Book.objects.create(
            filename=name, path=path, filesize=1, format='fb2', cat_type=0,
            docdate='', lang='en', title=name, search_title=name.upper(),
            annotation='', avail=2, catalog=catalog))
    return made


# --- export ----------------------------------------------------------------

@pytest.mark.django_db
def test_export_carries_each_kind_of_user_data(populated):
    payload = userdata.export()
    assert payload['version'] == userdata.VERSION
    assert len(payload['shelves']) == 1
    assert len(payload['themes']) == 1
    assert len(payload['kosync']) == 1
    assert len(payload['books']) == 1        # only the enriched/read one


@pytest.mark.django_db
def test_export_identifies_books_by_something_that_survives_a_rebuild(populated):
    shelf = userdata.export()['shelves'][0]
    assert shelf['path'] == 'shelf'
    assert shelf['filename'] == 'one.fb2'
    assert 'id' not in shelf


@pytest.mark.django_db
def test_export_skips_books_that_carry_nothing(populated):
    """A book with no enrichment and no reads is fully rebuilt by a scan."""
    exported = {b['filename'] for b in userdata.export()['books']}
    assert exported == {'one.fb2'}


@pytest.mark.django_db
def test_export_is_json_serialisable(populated):
    json.dumps(userdata.export())


# --- the round trip that matters -------------------------------------------

@pytest.mark.django_db
def test_a_rebuilt_catalogue_gets_its_user_data_back(populated):
    payload = userdata.export()
    old_id = populated['first'].id

    new_first, _ = rebuild(populated['cat'])
    assert new_first.id != old_id      # ids really did change
    assert not bookshelf.objects.exists()

    userdata.load(payload)

    shelf = bookshelf.objects.get()
    assert shelf.book_id == new_first.id
    assert shelf.status == bookshelf.STATUS_READING
    assert shelf.rating == 4
    assert shelf.percent == pytest.approx(0.42)
    assert shelf.position == '2.13'


@pytest.mark.django_db
def test_enrichment_survives_a_rebuild(populated):
    """Each of those fields cost a request to somebody else's API."""
    payload = userdata.export()
    rebuild(populated['cat'])

    userdata.load(payload)

    book = Book.objects.get(filename='one.fb2')
    assert book.isbn == '9780306406157'
    assert book.publisher == 'Gollancz'
    assert book.annotation == 'From Open Library.'
    assert book.enriched is not None


@pytest.mark.django_db
def test_counters_survive_a_rebuild(populated):
    payload = userdata.export()
    rebuild(populated['cat'])
    userdata.load(payload)

    stat = BookStat.objects.get()
    assert (stat.downloads, stat.reads) == (7, 3)


@pytest.mark.django_db
def test_theme_and_kosync_progress_survive(populated):
    payload = userdata.export()
    rebuild(populated['cat'])
    userdata.load(payload)

    assert Theme.objects.get().font_size == 120
    assert KosyncProgress.objects.get().percentage == pytest.approx(0.5)


# --- matching --------------------------------------------------------------

@pytest.mark.django_db
def test_a_renamed_book_is_found_by_its_content_digest(populated):
    """The (path, filename) pair does not survive a rename; the digest does."""
    BookDigest.objects.create(book=populated['first'], digest='a' * 32,
                              method=BookDigest.BINARY)
    payload = userdata.export()
    assert payload['shelves'][0]['digest'] == 'a' * 32

    renamed, = rebuild(populated['cat'], names=('renamed.fb2',))
    BookDigest.objects.create(book=renamed, digest='a' * 32, method=BookDigest.BINARY)

    userdata.load(payload)
    assert bookshelf.objects.get().book_id == renamed.id


@pytest.mark.django_db
def test_a_book_no_longer_in_the_catalogue_is_counted_not_guessed_at(populated):
    payload = userdata.export()
    rebuild(populated['cat'], names=('something-else.fb2',))

    stats = userdata.load(payload)
    assert stats['unknown_books'] >= 1
    assert not bookshelf.objects.exists()


@pytest.mark.django_db
def test_an_unknown_user_is_counted_not_created(populated, django_user_model):
    payload = userdata.export()
    django_user_model.objects.filter(username='reader').delete()

    stats = userdata.load(payload)
    assert stats['unknown_users'] >= 1
    assert not django_user_model.objects.filter(username='reader').exists()


# --- restoring onto a live catalogue ---------------------------------------

@pytest.mark.django_db
def test_an_existing_shelf_row_is_left_alone(populated):
    """Safe to run against a live catalogue, not only into an empty one."""
    payload = userdata.export()
    bookshelf.objects.update(rating=1, status=bookshelf.STATUS_READ)

    stats = userdata.load(payload)
    assert stats['skipped'] >= 1
    assert bookshelf.objects.get().rating == 1


@pytest.mark.django_db
def test_force_overwrites_it(populated):
    payload = userdata.export()
    bookshelf.objects.update(rating=1)

    userdata.load(payload, force=True)
    assert bookshelf.objects.get().rating == 4


@pytest.mark.django_db
def test_enrichment_already_present_is_not_replaced(populated):
    payload = userdata.export()
    Book.objects.filter(filename='one.fb2').update(publisher='From the file')

    userdata.load(payload)
    assert Book.objects.get(filename='one.fb2').publisher == 'From the file'


@pytest.mark.django_db
def test_counters_only_ever_go_up(populated):
    """Importing an old export must not erase what has been counted since."""
    payload = userdata.export()
    BookStat.objects.update(downloads=100)

    userdata.load(payload)
    assert BookStat.objects.get().downloads == 100


@pytest.mark.django_db
def test_dry_run_writes_nothing(populated):
    payload = userdata.export()
    rebuild(populated['cat'])

    stats = userdata.load(payload, dry_run=True)
    assert stats['shelves'] == 1
    assert not bookshelf.objects.exists()


@pytest.mark.django_db
def test_an_unknown_version_is_refused(populated):
    with pytest.raises(ValueError):
        userdata.load({'version': 999})


# --- the commands ----------------------------------------------------------

@pytest.mark.django_db
def test_the_commands_round_trip_through_a_file(populated, tmp_path):
    path = tmp_path / 'backup.json'
    call_command('sopds_userdata_export', '--output', str(path))
    assert path.exists()

    rebuild(populated['cat'])
    out = io.StringIO()
    call_command('sopds_userdata_import', str(path), stdout=out)

    assert 'Restored' in out.getvalue()
    assert bookshelf.objects.get().rating == 4


@pytest.mark.django_db
def test_export_to_stdout_is_valid_json_on_its_own(populated):
    """The summary goes to stderr so it cannot end up inside a redirected file."""
    out = io.StringIO()
    call_command('sopds_userdata_export', stdout=out)
    payload = json.loads(out.getvalue())
    assert payload['version'] == userdata.VERSION


@pytest.mark.django_db
def test_importing_a_missing_file_is_an_error_not_a_traceback(populated):
    from django.core.management.base import CommandError
    with pytest.raises(CommandError):
        call_command('sopds_userdata_import', '/nonexistent/backup.json')
