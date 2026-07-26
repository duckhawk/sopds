"""Open Library enrichment: the API client and the sopds_enrich command.

No test touches the network — `openlibrary.fetch` is given a fake session, or
the command is given a fake `fetch`.
"""
import json

import pytest
import requests
from django.core.management import call_command
from django.utils import timezone

from opds_catalog import openlibrary
from opds_catalog import opdsdb
from opds_catalog.models import Author, Book, Catalog

ISBN = '9780306406157'
OTHER = '9783161484100'


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('%s' % self.status_code)

    def json(self):
        if isinstance(self._payload, str):
            return json.loads(self._payload)   # raises ValueError on junk
        return self._payload


class FakeSession:
    """Records the calls made and replays a canned response."""

    def __init__(self, payload=None, exc=None, status=200):
        self.payload = payload if payload is not None else {}
        self.exc = exc
        self.status = status
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({'url': url, 'params': params, 'headers': headers})
        if self.exc:
            raise self.exc
        return FakeResponse(self.payload, self.status)


def details_payload(isbn, **details):
    return {'ISBN:%s' % isbn: {'info_url': 'https://openlibrary.org/x', 'details': details}}


# --- client ---------------------------------------------------------------

def test_parse_details_reads_a_plain_string_description():
    assert openlibrary.parse_details({'description': ' A book. '})['annotation'] == 'A book.'


def test_parse_details_reads_a_typed_text_description():
    fields = openlibrary.parse_details({'description': {'type': '/type/text', 'value': 'A book.'}})
    assert fields['annotation'] == 'A book.'


def test_parse_details_takes_the_first_publisher_and_the_date():
    fields = openlibrary.parse_details({'publishers': ['Gollancz', 'Orion'], 'publish_date': '1982'})
    assert fields == {'publisher': 'Gollancz', 'docdate': '1982'}


def test_parse_details_omits_fields_open_library_does_not_have():
    assert openlibrary.parse_details({'number_of_pages': 300}) == {}


def test_fetch_asks_for_every_isbn_and_identifies_itself():
    session = FakeSession(details_payload(ISBN, publish_date='1982'))
    openlibrary.fetch([ISBN, OTHER], session=session)

    params = session.calls[0]['params']
    assert params['bibkeys'] == 'ISBN:%s,ISBN:%s' % (ISBN, OTHER)
    assert params['jscmd'] == 'details'
    assert 'Lectern' in session.calls[0]['headers']['User-Agent']


def test_fetch_keys_the_result_by_bare_isbn():
    session = FakeSession(details_payload(ISBN, publish_date='1982'))
    assert openlibrary.fetch([ISBN], session=session) == {ISBN: {'docdate': '1982'}}


def test_fetch_skips_an_isbn_open_library_has_nothing_for():
    session = FakeSession({'ISBN:%s' % ISBN: {'details': {}}})
    assert openlibrary.fetch([ISBN], session=session) == {}


def test_fetch_makes_no_request_for_an_empty_batch():
    session = FakeSession()
    assert openlibrary.fetch([], session=session) == {}
    assert session.calls == []


@pytest.mark.parametrize('session', [
    FakeSession(exc=requests.ConnectionError('down')),
    FakeSession(exc=requests.Timeout('slow')),
    FakeSession(status=503),
    FakeSession(payload='not json'),
    FakeSession(payload=['unexpected shape']),
])
def test_fetch_reports_failure_as_none_instead_of_raising(session):
    """One bad batch must not abort a run — but None, not {}, so the caller can
    tell a failure from "Open Library has nothing"."""
    assert openlibrary.fetch([ISBN], session=session) is None


# --- command --------------------------------------------------------------

@pytest.fixture
def catalogue(db):
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)

    def add(title, isbn='', annotation='', docdate='', publisher=''):
        return Book.objects.create(
            filename='%s.fb2' % title, path='.', filesize=1, format='fb2', cat_type=0,
            lang='en', title=title, search_title=title.upper(), avail=2, catalog=cat,
            isbn=isbn, annotation=annotation, docdate=docdate, publisher=publisher,
        )

    return add


@pytest.fixture
def fake_api(monkeypatch):
    """Replace the network call; record which ISBNs were asked for."""
    state = {'asked': [], 'answer': {}}

    def fetch(isbns, **kwargs):
        state['asked'].extend(isbns)
        return {i: state['answer'][i] for i in isbns if i in state['answer']}

    monkeypatch.setattr(openlibrary, 'fetch', fetch)
    return state


@pytest.mark.django_db
def test_fills_the_empty_fields(catalogue, fake_api):
    book = catalogue('Bare book', isbn=ISBN)
    fake_api['answer'] = {ISBN: {'annotation': 'A description.', 'publisher': 'Gollancz', 'docdate': '1982'}}

    call_command('sopds_enrich', '--sleep', '0')

    book.refresh_from_db()
    assert book.annotation == 'A description.'
    assert book.publisher == 'Gollancz'
    assert book.docdate == '1982'
    assert book.enriched is not None


@pytest.mark.django_db
def test_the_book_file_wins_over_open_library(catalogue, fake_api):
    """A value parsed out of the file is authoritative; an ISBN can be shared by
    editions that differ in the details."""
    book = catalogue('Described book', isbn=ISBN, annotation='From the FB2 file.', docdate='1979')
    fake_api['answer'] = {ISBN: {'annotation': 'From Open Library.', 'docdate': '1982', 'publisher': 'Gollancz'}}

    call_command('sopds_enrich', '--sleep', '0')

    book.refresh_from_db()
    assert book.annotation == 'From the FB2 file.'
    assert book.docdate == '1979'
    assert book.publisher == 'Gollancz'      # this one was empty, so it is filled


@pytest.mark.django_db
def test_force_overwrites(catalogue, fake_api):
    book = catalogue('Described book', isbn=ISBN, annotation='From the FB2 file.')
    fake_api['answer'] = {ISBN: {'annotation': 'From Open Library.'}}

    call_command('sopds_enrich', '--force', '--sleep', '0')

    book.refresh_from_db()
    assert book.annotation == 'From Open Library.'


@pytest.mark.django_db
def test_books_without_an_isbn_are_never_looked_up(catalogue, fake_api):
    catalogue('No isbn at all')
    call_command('sopds_enrich', '--sleep', '0')
    assert fake_api['asked'] == []


@pytest.mark.django_db
def test_a_second_run_does_not_re_query(catalogue, fake_api):
    """Including books Open Library had nothing for — otherwise every run pays
    for the same misses again."""
    catalogue('Unknown book', isbn=ISBN)
    call_command('sopds_enrich', '--sleep', '0')
    assert fake_api['asked'] == [ISBN]

    fake_api['asked'] = []
    call_command('sopds_enrich', '--sleep', '0')
    assert fake_api['asked'] == []


@pytest.mark.django_db
def test_force_re_queries_an_already_enriched_book(catalogue, fake_api):
    catalogue('Book', isbn=ISBN)
    call_command('sopds_enrich', '--sleep', '0')

    fake_api['asked'] = []
    call_command('sopds_enrich', '--force', '--sleep', '0')
    assert fake_api['asked'] == [ISBN]


@pytest.mark.django_db
def test_one_isbn_shared_by_several_rows_is_queried_once_and_applied_to_all(catalogue, fake_api):
    fb2 = catalogue('Book fb2', isbn=ISBN)
    epub = catalogue('Book epub', isbn=ISBN)
    fake_api['answer'] = {ISBN: {'publisher': 'Gollancz'}}

    call_command('sopds_enrich', '--sleep', '0')

    assert fake_api['asked'] == [ISBN]
    fb2.refresh_from_db()
    epub.refresh_from_db()
    assert fb2.publisher == epub.publisher == 'Gollancz'


@pytest.mark.django_db
def test_dry_run_writes_nothing(catalogue, fake_api):
    book = catalogue('Bare book', isbn=ISBN)
    fake_api['answer'] = {ISBN: {'publisher': 'Gollancz'}}

    call_command('sopds_enrich', '--dry-run', '--sleep', '0')

    book.refresh_from_db()
    assert book.publisher == ''
    assert book.enriched is None


@pytest.mark.django_db
def test_limit_caps_the_work(catalogue, fake_api):
    for n in range(5):
        catalogue('Book %d' % n, isbn=ISBN if n == 0 else OTHER)

    call_command('sopds_enrich', '--limit', '2', '--sleep', '0')
    assert len(fake_api['asked']) == 2


@pytest.mark.django_db
def test_an_overlong_value_is_truncated_to_the_column(catalogue, fake_api):
    book = catalogue('Bare book', isbn=ISBN)
    fake_api['answer'] = {ISBN: {'publisher': 'P' * 500}}

    call_command('sopds_enrich', '--sleep', '0')

    book.refresh_from_db()
    assert len(book.publisher) == 128


@pytest.mark.django_db
def test_a_failed_lookup_leaves_the_book_a_candidate(catalogue, monkeypatch):
    """A momentary outage must not permanently mark the batch as looked up."""
    book = catalogue('Book', isbn=ISBN)
    monkeypatch.setattr(openlibrary, 'fetch', lambda isbns, **kw: None)

    call_command('sopds_enrich', '--sleep', '0')

    book.refresh_from_db()
    assert book.enriched is None

    asked = []

    def fetch(isbns, **kwargs):
        asked.extend(isbns)
        return {ISBN: {'publisher': 'Gollancz'}}

    monkeypatch.setattr(openlibrary, 'fetch', fetch)
    call_command('sopds_enrich', '--sleep', '0')

    assert asked == [ISBN]
    book.refresh_from_db()
    assert book.publisher == 'Gollancz'


@pytest.mark.django_db
def test_a_book_already_enriched_by_hand_is_left_alone(catalogue, fake_api):
    book = catalogue('Book', isbn=ISBN, annotation='a', docdate='b', publisher='c')
    book.enriched = timezone.now()
    book.save()

    call_command('sopds_enrich', '--sleep', '0')
    assert fake_api['asked'] == []


# --- authors ---------------------------------------------------------------

@pytest.mark.django_db
def test_a_book_with_no_author_gets_one(catalogue, fake_api):
    book = catalogue('Anonymous book', isbn=ISBN)
    fake_api['answer'] = {ISBN: {'authors': ['Ellis Peters']}}

    call_command('sopds_enrich', '--sleep', '0')

    assert [a.full_name for a in book.authors.all()] == ['Ellis Peters']


@pytest.mark.django_db
def test_several_authors_are_all_attached(catalogue, fake_api):
    book = catalogue('Collaboration', isbn=ISBN)
    fake_api['answer'] = {ISBN: {'authors': ['Arkady Strugatsky', 'Boris Strugatsky']}}

    call_command('sopds_enrich', '--sleep', '0')

    assert sorted(a.full_name for a in book.authors.all()) == \
        ['Arkady Strugatsky', 'Boris Strugatsky']


@pytest.mark.django_db
def test_an_existing_author_is_never_replaced(catalogue, fake_api):
    """The parser read that name out of the file, and one ISBN can cover
    editions credited differently. Not even --force overrides it."""
    book = catalogue('Credited book', isbn=ISBN)
    book.authors.add(opdsdb.addauthor('Real Author'))
    fake_api['answer'] = {ISBN: {'authors': ['Someone Else']}}

    call_command('sopds_enrich', '--force', '--sleep', '0')

    assert [a.full_name for a in book.authors.all()] == ['Real Author']


@pytest.mark.django_db
def test_an_author_row_is_reused_not_duplicated(catalogue, fake_api):
    first = catalogue('One', isbn=ISBN)
    second = catalogue('Two', isbn=OTHER)
    fake_api['answer'] = {ISBN: {'authors': ['Ellis Peters']},
                          OTHER: {'authors': ['Ellis Peters']}}

    call_command('sopds_enrich', '--sleep', '0')

    assert Author.objects.filter(full_name='Ellis Peters').count() == 1
    assert first.authors.first() == second.authors.first()


@pytest.mark.django_db
def test_the_author_gets_its_search_name_filled(catalogue, fake_api):
    """Otherwise the new author is invisible to the author search."""
    catalogue('Anonymous book', isbn=ISBN)
    fake_api['answer'] = {ISBN: {'authors': ['Ellis Peters']}}

    call_command('sopds_enrich', '--sleep', '0')

    author = Author.objects.get(full_name='Ellis Peters')
    assert author.search_full_name == 'ELLIS PETERS'


@pytest.mark.django_db
def test_a_book_needing_only_authors_is_still_a_candidate(catalogue, fake_api):
    """It has every scalar field filled, so the old query skipped it."""
    catalogue('Complete but anonymous', isbn=ISBN,
              annotation='a', docdate='1982', publisher='Gollancz')
    fake_api['answer'] = {ISBN: {'authors': ['Ellis Peters']}}

    call_command('sopds_enrich', '--sleep', '0')

    assert fake_api['asked'] == [ISBN]
    assert Author.objects.filter(full_name='Ellis Peters').exists()


@pytest.mark.django_db
def test_dry_run_attaches_nobody(catalogue, fake_api):
    book = catalogue('Anonymous book', isbn=ISBN)
    fake_api['answer'] = {ISBN: {'authors': ['Ellis Peters']}}

    call_command('sopds_enrich', '--dry-run', '--sleep', '0')

    assert not book.authors.exists()
    assert not Author.objects.exists()


@pytest.mark.django_db
def test_an_absurd_author_list_is_capped(catalogue, fake_api):
    book = catalogue('Anthology', isbn=ISBN)
    fake_api['answer'] = {ISBN: {'authors': ['Author %d' % n for n in range(50)]}}

    call_command('sopds_enrich', '--sleep', '0')

    assert book.authors.count() == 10


def test_parse_details_reads_author_names():
    fields = openlibrary.parse_details(
        {'authors': [{'name': 'Ellis Peters', 'key': '/authors/OL1A'}]})
    assert fields['authors'] == ['Ellis Peters']


def test_parse_details_ignores_malformed_author_entries():
    fields = openlibrary.parse_details(
        {'authors': [{'key': '/authors/OL1A'}, None, 'plain string', {'name': '  '}]})
    assert 'authors' not in fields
