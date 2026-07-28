# -*- coding: utf-8 -*-
"""Two-way reading-position sync with Moon+ Reader.

Covers the ``.po`` marker itself, the check that decides whether the copy on the
phone is the copy we hold, and both directions of the exchange: a marker
uploaded over WebDAV landing on the shelf, and a position saved in the browser
reader being written back into the file the phone reads.

The markers quoted here are the ones a real Moon+ Reader Pro wrote.
"""
import base64
import os

import pytest
from constance import config

from opds_catalog.models import Author, Book, Catalog, bauthor, bookshelf
from sopds_sync import linking, moonpos, moonreader, moonsync
from sopds_sync.models import MoonReaderPosition

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'opds_catalog', 'tests', 'data')
FB2 = '262001.fb2'
CACHE = 'Apps/Books/.Moon+/Cache'
DEVICE = 1784531106148


def basic(username='moon', password='pw123456'):
    token = base64.b64encode(('%s:%s' % (username, password)).encode()).decode()
    return {'HTTP_AUTHORIZATION': 'Basic %s' % token}


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username='moon', password='pw123456')


@pytest.fixture(autouse=True)
def dav_env(db, tmp_path):
    config.SOPDS_WEBDAV_ENABLE = True
    config.SOPDS_WEBDAV_ROOT = str(tmp_path)
    config.SOPDS_ROOT_LIB = DATA
    from django.core.cache import cache
    cache.clear()
    return tmp_path


@pytest.fixture
def book(db):
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    return Book.objects.create(
        filename=FB2, path='.', filesize=os.path.getsize(os.path.join(DATA, FB2)),
        format='fb2', cat_type=0, docdate='2011', lang='en',
        title='The Sanctuary Sparrow', search_title='THE SANCTUARY SPARROW',
        annotation='', avail=2, catalog=cat,
    )


def outline_for(book, rule='all'):
    for candidate in moonpos.candidates(book):
        if candidate.rule == rule:
            return candidate
    raise AssertionError('no %s outline' % rule)


def marker_at(outline, chapter, offset):
    """A marker whose percentage is the one these coordinates really imply."""
    return moonreader.Marker(DEVICE, chapter, 0, offset,
                             round(outline.percent_at(chapter, offset), 1))


def put_marker(client, name, marker, **extra):
    return client.generic(
        'PUT', '/dav/%s/%s.po' % (CACHE, name), str(marker).encode('utf-8'),
        **dict(basic(), **extra))


def mkcol(client, path):
    return client.generic('MKCOL', '/dav/%s' % path, **basic())


def make_cache_dir(client):
    for depth in ('Apps', 'Apps/Books', 'Apps/Books/.Moon+', CACHE):
        mkcol(client, depth)


# --- the marker ------------------------------------------------------------

@pytest.mark.parametrize('text, chapter, volume, offset, percent', [
    ('1784531106148*11@0#0:23.1%', 11, 0, 0, 23.1),
    ('1784531106148*3@0#8527:3.0%', 3, 0, 8527, 3.0),
    ('1784531106148*0@0#2130:0.2%', 0, 0, 2130, 0.2),
    ('1502878256645*28@0#15529:12.3%', 28, 0, 15529, 12.3),
])
def test_parse_real_markers(text, chapter, volume, offset, percent):
    marker = moonreader.parse(text)
    assert (marker.chapter, marker.volume, marker.offset) == (chapter, volume, offset)
    assert marker.percent == pytest.approx(percent)
    assert marker.device == int(text.split('*')[0])
    # What we write has to be byte-identical to what Moon+ Reader wrote.
    assert str(marker) == text


def test_fraction_is_clamped_and_scaled():
    assert moonreader.parse('1*0@0#0:23.1%').fraction == pytest.approx(0.231)
    assert moonreader.parse('1*0@0#0:0%').fraction == 0.0


@pytest.mark.parametrize('text', [
    '', None, 'not a marker', '1784531106148*11@0#0:23.1',      # no percent sign
    '1784531106148*11#0:23.1%',                                  # no volume
    '*11@0#0:23.1%', '1784531106148*-1@0#0:23.1%',
])
def test_parse_rejects_junk(text):
    assert moonreader.parse(text) is None


def test_parse_survives_undecodable_bytes():
    assert moonreader.parse(b'\xff\xfe not utf-8') is None


@pytest.mark.parametrize('path, expected', [
    ('Apps/Books/.Moon+/Cache/Book.fb2.po', True),
    ('Apps\\Books\\.Moon+\\Cache\\Book.fb2.po', True),
    ('Apps/Books/.Moon+/Cache/Book.fb2.an', False),   # annotations, not a position
    ('locale/ru/LC_MESSAGES/django.po', False),       # a gettext catalogue
    ('Apps/Books/Book.fb2.po', False),
])
def test_is_position_file(path, expected):
    assert moonreader.is_position_file(path) is expected


def test_book_name_strips_the_marker_suffix():
    assert moonreader.book_name(
        'Apps/Books/.Moon+/Cache/Город Бездны - Рейнольдс Аластер.fb2.po'
    ) == 'Город Бездны - Рейнольдс Аластер.fb2'


# --- deciding whether the phone's copy is ours -----------------------------

@pytest.mark.django_db
def test_fit_accepts_the_rule_that_reproduces_the_percentage(book):
    outline = outline_for(book)
    fitted = moonpos.fit(book, marker_at(outline, 5, 400))
    assert fitted is not None
    assert fitted.percent_at(5, 400) == pytest.approx(outline.percent_at(5, 400))


@pytest.mark.django_db
def test_fit_rejects_a_marker_from_a_different_edition(book):
    # Coordinates that cannot produce this percentage against our copy: the
    # phone is reading some other edition of the book.
    assert moonpos.fit(book, moonreader.parse('1*5@0#400:99.0%')) is None


@pytest.mark.django_db
def test_fit_rejects_a_chapter_our_copy_does_not_have(book):
    assert moonpos.fit(book, moonreader.parse('1*4000@0#0:50.0%')) is None


@pytest.mark.django_db
def test_non_fb2_has_no_outline(book):
    book.format = 'epub'
    book.save()
    assert moonpos.candidates(book) is None


@pytest.mark.django_db
def test_start_of_a_chapter_resumes_inside_that_chapter(book):
    # A chapter's character count begins ahead of its first paragraph, so an
    # offset of 0 must not resolve to the closing paragraph of the chapter
    # before it — which is what "the last paragraph at or before this offset"
    # gives on its own.
    outline = outline_for(book)
    for chapter in range(1, len(outline.starts)):
        pid = outline.resume_paragraph(chapter, 0)
        assert outline.char_at(pid) >= outline.starts[chapter], chapter
        if chapter + 1 < len(outline.starts):
            assert outline.char_at(pid) < outline.starts[chapter + 1], chapter


@pytest.mark.django_db
def test_resume_paragraph_rejects_a_chapter_out_of_range(book):
    assert outline_for(book).resume_paragraph(9999, 0) is None


@pytest.mark.django_db
def test_outline_survives_the_cache(book):
    # Write-back reads the outline back out of the cache on every saved
    # position rather than reparsing the book, and in production that cache is
    # Redis — so it has to pickle and come back whole.
    built = moonpos.for_rule(book, 'all')
    again = moonpos.for_rule(book, 'all')
    assert again is not built
    assert (again.rule, again.starts, again.total) == (built.rule, built.starts, built.total)
    assert again.paragraphs == built.paragraphs


@pytest.mark.django_db
def test_for_rule_rejects_a_rule_that_does_not_exist(book):
    assert moonpos.for_rule(book, 'invented') is None


@pytest.mark.django_db
def test_paragraph_at_returns_the_paragraph_being_read(book):
    outline = outline_for(book)
    pid = next(iter(outline.paragraphs))
    start = outline.paragraphs[pid]
    # A position part-way into a paragraph resumes at that paragraph, not the
    # next one, because the text above the fold belongs to it.
    assert outline.paragraph_at(start) == pid
    assert outline.char_at(pid) == start


# --- matching a device file name to a catalogue book -----------------------

@pytest.mark.django_db
def test_resolve_by_catalogue_filename(book):
    assert linking.resolve_book_by_name(FB2) == book


@pytest.mark.django_db
def test_resolve_ignores_the_zip_moon_reads_through(book):
    assert linking.resolve_book_by_name(FB2 + '.zip') == book


@pytest.mark.django_db
def test_resolve_by_title_and_author(book):
    author = Author.objects.create(full_name='Peters Ellis', search_full_name='PETERS ELLIS')
    bauthor.objects.create(book=book, author=author)
    assert linking.resolve_book_by_name('The Sanctuary Sparrow - Peters Ellis.fb2') == book


def other_edition(book, filename='other.fb2'):
    """A second catalogue row for the same work — a different file entirely."""
    return Book.objects.create(
        filename=filename, path='.', filesize=1, format='fb2', cat_type=0,
        docdate='2011', lang='en', title='The Sanctuary Sparrow',
        search_title='THE SANCTUARY SPARROW', annotation='', avail=2,
        catalog=book.catalog,
    )


@pytest.mark.django_db
def test_resolve_returns_every_edition_of_an_ambiguous_name(book):
    # A catalogue built from a public library dump holds the same novel several
    # times; the name cannot tell them apart, so all of them come back and the
    # marker decides.
    other = other_edition(book)
    found = linking.resolve_books_by_name('The Sanctuary Sparrow.fb2')
    assert {b.id for b in found} == {book.id, other.id}
    assert linking.resolve_book_by_name('The Sanctuary Sparrow.fb2') is None


@pytest.mark.django_db
def test_a_definite_name_does_not_drag_in_other_editions(book):
    other_edition(book)
    # The catalogue's own file name identifies one file, so there is nothing
    # to weigh up.
    assert linking.resolve_books_by_name(FB2) == [book]


@pytest.mark.django_db
def test_resolve_unknown_name(book):
    assert linking.resolve_book_by_name('Something Else - Nobody.fb2') is None


@pytest.mark.django_db
def test_the_marker_picks_the_edition_the_phone_is_reading(client, user, book, monkeypatch):
    # Two editions under one name, the one that cannot possibly be the phone's
    # copy offered first. What decides is the marker reproducing itself against
    # a candidate, not the name and not the order the rows arrive in.
    decoy = other_edition(book, filename='missing.fb2')
    monkeypatch.setattr(linking, 'resolve_books_by_name', lambda name: [decoy, book])
    make_cache_dir(client)
    outline = outline_for(book)

    put_marker(client, 'The Sanctuary Sparrow.fb2', marker_at(outline, 5, 400))

    row = MoonReaderPosition.objects.get(user=user)
    assert row.book == book
    assert row.rule
    assert bookshelf.objects.get(user=user, book=book).position == \
        outline.resume_paragraph(5, 400)


@pytest.mark.django_db
def test_an_ambiguous_name_that_fits_nothing_credits_no_book(client, user, book):
    other_edition(book)
    make_cache_dir(client)
    # Several editions, and the coordinates describe none of them: crediting
    # one at random would be a silent guess.
    put_marker(client, 'The Sanctuary Sparrow.fb2', moonreader.parse('1*5@0#400:99.0%'))

    assert MoonReaderPosition.objects.get(user=user).book is None
    assert not bookshelf.objects.exists()


# --- in: the phone uploads a position --------------------------------------

@pytest.mark.django_db
def test_upload_records_progress_and_the_reading_position(client, user, book):
    make_cache_dir(client)
    outline = outline_for(book)
    marker = marker_at(outline, 5, 400)

    assert put_marker(client, FB2, marker).status_code == 201

    shelf = bookshelf.objects.get(user=user, book=book)
    assert shelf.percent == pytest.approx(marker.fraction, abs=1e-4)
    assert shelf.status == bookshelf.STATUS_READING
    # The coordinates checked out, so the browser reader knows where to open.
    assert shelf.position == outline.resume_paragraph(5, 400)

    row = MoonReaderPosition.objects.get(user=user, book=book)
    assert row.marker == str(marker)
    assert row.rule


@pytest.mark.django_db
def test_upload_from_another_edition_syncs_percent_only(client, user, book):
    make_cache_dir(client)
    assert put_marker(client, FB2, moonreader.parse('1*5@0#400:42.0%')).status_code == 201

    shelf = bookshelf.objects.get(user=user, book=book)
    assert shelf.percent == pytest.approx(0.42)
    # Nothing is claimed about where in the text that is.
    assert shelf.position is None
    assert MoonReaderPosition.objects.get(user=user, book=book).rule == ''


@pytest.mark.django_db
def test_upload_of_an_unmatched_book_is_still_stored(client, user, book):
    make_cache_dir(client)
    r = put_marker(client, 'Not In The Catalogue.fb2', moonreader.parse('1*1@0#5:7.0%'))
    assert r.status_code == 201
    row = MoonReaderPosition.objects.get(user=user)
    assert row.book is None and row.name == 'Not In The Catalogue.fb2'
    assert not bookshelf.objects.exists()


@pytest.mark.django_db
def test_unreadable_marker_is_stored_without_complaint(client, user, book):
    make_cache_dir(client)
    r = client.generic('PUT', '/dav/%s/%s.po' % (CACHE, FB2), b'gibberish', **basic())
    assert r.status_code == 201
    assert client.get('/dav/%s/%s.po' % (CACHE, FB2), **basic()).status_code == 200
    assert not MoonReaderPosition.objects.exists()


@pytest.mark.django_db
def test_a_plain_file_is_not_read_as_a_position(client, user, book):
    r = client.generic('PUT', '/dav/notes.txt', b'1*5@0#400:42.0%', **basic())
    assert r.status_code == 201
    assert not MoonReaderPosition.objects.exists()


@pytest.mark.django_db
def test_progress_only_moves_forward(client, user, book):
    make_cache_dir(client)
    outline = outline_for(book)
    put_marker(client, FB2, marker_at(outline, 8, 0))
    ahead = bookshelf.objects.get(user=user, book=book)
    # A second device reporting a stale position must not wind the shelf back —
    # neither the percentage nor the place in the text.
    put_marker(client, FB2, marker_at(outline, 3, 0))
    now = bookshelf.objects.get(user=user, book=book)
    assert now.percent == pytest.approx(ahead.percent)
    assert now.position == ahead.position


# --- out: the browser reader saves a position ------------------------------

@pytest.mark.django_db
def test_browser_position_is_written_back_for_the_phone(client, user, book):
    make_cache_dir(client)
    outline = outline_for(book)
    put_marker(client, FB2, marker_at(outline, 5, 400))

    target = [pid for pid, start in outline.paragraphs.items()
              if start > outline.starts[8]][0]
    client.login(username='moon', password='pw123456')
    assert client.get('/web/bs/setpos/%d/?pos=%s' % (book.id, target)).status_code == 200

    path = os.path.join(str(config.SOPDS_WEBDAV_ROOT), str(user.id),
                        CACHE, FB2 + '.po')
    with open(path, encoding='utf-8') as handle:
        written = moonreader.parse(handle.read())

    expected_chapter, expected_offset, _ = outline.locate(outline.char_at(target))
    assert (written.chapter, written.offset) == (expected_chapter, expected_offset)
    # The device id Moon+ Reader stamped its files with is left alone.
    assert written.device == DEVICE


@pytest.mark.django_db
def test_nothing_is_written_back_without_a_confirmed_edition(client, user, book):
    make_cache_dir(client)
    put_marker(client, FB2, moonreader.parse('1*5@0#400:42.0%'))
    before = open(os.path.join(str(config.SOPDS_WEBDAV_ROOT), str(user.id),
                               CACHE, FB2 + '.po'), encoding='utf-8').read()

    outline = outline_for(book)
    target = next(iter(outline.paragraphs))
    client.login(username='moon', password='pw123456')
    client.get('/web/bs/setpos/%d/?pos=%s' % (book.id, target))

    after = open(os.path.join(str(config.SOPDS_WEBDAV_ROOT), str(user.id),
                              CACHE, FB2 + '.po'), encoding='utf-8').read()
    assert after == before


@pytest.mark.django_db
def test_no_marker_file_means_no_write(client, user, book):
    # A book never synced to a phone has nowhere to write to, and we must not
    # invent a file for a book Moon+ Reader may not even hold.
    client.login(username='moon', password='pw123456')
    assert client.get('/web/bs/setpos/%d/?pos=1.1' % book.id).status_code == 200
    assert not os.path.exists(os.path.join(str(config.SOPDS_WEBDAV_ROOT), str(user.id)))


@pytest.mark.django_db
def test_publish_leaves_the_file_alone_when_nothing_moved(client, user, book):
    make_cache_dir(client)
    outline = outline_for(book)
    marker = marker_at(outline, 5, 400)
    put_marker(client, FB2, marker)

    path = os.path.join(str(config.SOPDS_WEBDAV_ROOT), str(user.id), CACHE, FB2 + '.po')
    mtime = os.path.getmtime(path)
    # The phone's offset points part-way into a paragraph; the browser reader
    # can only name the paragraph. Writing that back would rewind the device to
    # the paragraph's first character, and would do it again on every sync.
    same = outline.resume_paragraph(5, 400)
    assert moonsync.publish(user, book, same) is False
    assert os.path.getmtime(path) == mtime
    assert moonreader.parse(open(path, encoding='utf-8').read()).offset == 400
