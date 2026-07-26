"""OPDS 2.0, the JSON catalogue format.

The shape asserted here was taken from the official test catalogue at
https://test.opds.io/2.0/home.json, not from the specification prose — the
published draft currently 404s, and a live reference implementation is the
better thing to match.
"""
import json
import os

import pytest
from django.urls import reverse
from constance import config

from opds_catalog import opds2, opdsdb, stats
from opds_catalog.models import Book, Catalog, Counter, bookshelf

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


@pytest.fixture
def catalogue(db, django_user_model):
    config.SOPDS_AUTH = False
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_MAXITEMS = 60
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)

    def add(title, **extra):
        fields = dict(filename='%s.fb2' % title, path='.', filesize=1, format='fb2',
                      cat_type=0, docdate='2011', lang='en', title=title,
                      search_title=title.upper(), annotation='', avail=2, catalog=cat)
        fields.update(extra)
        return Book.objects.create(**fields)

    first = add('The Sanctuary Sparrow', isbn='9780306406157',
                publisher='Gollancz', annotation='A mystery.')
    first.authors.add(opdsdb.addauthor('Ellis Peters'))
    second = add('Another Book')
    Counter.objects.update_known_counters()
    return {'first': first, 'second': second, 'cat': cat}


def feed(client, name, **params):
    resp = client.get(reverse(name), params)
    assert resp.status_code == 200
    assert resp['Content-Type'].startswith('application/opds+json')
    return json.loads(resp.content.decode())


# --- the navigation root ---------------------------------------------------

@pytest.mark.django_db
def test_the_root_is_a_navigation_feed(client, catalogue):
    body = feed(client, 'opds:opds2_root')
    assert body['metadata']['title']
    assert {'navigation', 'links', 'metadata'} <= set(body)
    assert all({'href', 'title', 'type'} <= set(e) for e in body['navigation'])


@pytest.mark.django_db
def test_every_feed_carries_a_self_link(client, catalogue):
    for name in ('opds2_root', 'opds2_new', 'opds2_rated', 'opds2_popular', 'opds2_books'):
        body = feed(client, 'opds:' + name)
        rels = {link['rel'] for link in body['links']}
        assert 'self' in rels, name


@pytest.mark.django_db
def test_search_is_advertised_as_a_uri_template(client, catalogue):
    """2.0 uses a templated link rather than an OpenSearch document."""
    link = next(l for l in feed(client, 'opds:opds2_root')['links'] if l['rel'] == 'search')
    assert link['templated'] is True
    assert link['href'].endswith('{?query}')


@pytest.mark.django_db
def test_urls_are_absolute(client, catalogue):
    """Several 2.0 clients insist on it."""
    body = feed(client, 'opds:opds2_root')
    assert all(e['href'].startswith('http') for e in body['navigation'])


@pytest.mark.django_db
def test_the_atom_root_points_at_the_json_one(client, catalogue):
    """So a client that prefers JSON can find it without being told."""
    body = client.get(reverse('opds:main')).content.decode()
    assert 'application/opds+json' in body
    assert reverse('opds:opds2_root') in body


# --- publications ----------------------------------------------------------

@pytest.mark.django_db
def test_a_publication_has_the_reference_shape(client, catalogue):
    pub = feed(client, 'opds:opds2_new')['publications'][0]
    assert {'metadata', 'links', 'images'} <= set(pub)
    assert pub['metadata']['@type'] == 'http://schema.org/Book'
    assert pub['metadata']['title']
    assert pub['metadata']['identifier']


@pytest.mark.django_db
def test_metadata_is_carried_across(client, catalogue):
    pubs = {p['metadata']['title']: p for p in feed(client, 'opds:opds2_new')['publications']}
    meta = pubs['The Sanctuary Sparrow']['metadata']

    assert meta['identifier'] == 'urn:isbn:9780306406157'
    assert meta['publisher'] == 'Gollancz'
    assert meta['description'] == 'A mystery.'
    assert meta['language'] == 'en'
    assert meta['author'] == {'name': 'Ellis Peters'}


@pytest.mark.django_db
def test_a_book_without_an_isbn_still_gets_an_identifier(client, catalogue):
    pubs = {p['metadata']['title']: p for p in feed(client, 'opds:opds2_new')['publications']}
    assert pubs['Another Book']['metadata']['identifier'].startswith('urn:lectern:book:')


@pytest.mark.django_db
def test_absent_metadata_is_omitted_not_empty(client, catalogue):
    """A client should not have to distinguish "" from "unknown"."""
    pubs = {p['metadata']['title']: p for p in feed(client, 'opds:opds2_new')['publications']}
    meta = pubs['Another Book']['metadata']
    assert 'publisher' not in meta
    assert 'description' not in meta
    assert 'author' not in meta


@pytest.mark.django_db
def test_several_authors_become_a_list(client, catalogue):
    book = catalogue['second']
    book.authors.add(opdsdb.addauthor('Arkady Strugatsky'))
    book.authors.add(opdsdb.addauthor('Boris Strugatsky'))

    pubs = {p['metadata']['title']: p for p in feed(client, 'opds:opds2_new')['publications']}
    authors = pubs['Another Book']['metadata']['author']
    assert isinstance(authors, list) and len(authors) == 2


@pytest.mark.django_db
def test_acquisition_and_image_links(client, catalogue):
    pub = feed(client, 'opds:opds2_new')['publications'][0]

    rels = {link['rel'] for link in pub['links']}
    assert opds2.ACQUISITION in rels
    image_rels = {i['rel'] for i in pub['images']}
    assert {opds2.IMAGE, opds2.THUMBNAIL} == image_rels


# --- the listings match the Atom ones --------------------------------------

@pytest.mark.django_db
def test_recently_added_is_newest_first(client, catalogue):
    titles = [p['metadata']['title'] for p in feed(client, 'opds:opds2_new')['publications']]
    assert titles == ['Another Book', 'The Sanctuary Sparrow']


@pytest.mark.django_db
def test_popular_uses_the_same_ordering_as_the_atom_feed(client, catalogue):
    stats.record(catalogue['first'].id, stats.DOWNLOADS)
    titles = [p['metadata']['title'] for p in feed(client, 'opds:opds2_popular')['publications']]
    assert titles == ['The Sanctuary Sparrow']


@pytest.mark.django_db
def test_top_rated_uses_the_same_ordering(client, catalogue, django_user_model):
    user = django_user_model.objects.create_user(username='r', password='pw')
    bookshelf.objects.create(user=user, book=catalogue['second'], rating=5)
    titles = [p['metadata']['title'] for p in feed(client, 'opds:opds2_rated')['publications']]
    assert titles == ['Another Book']


# --- pagination ------------------------------------------------------------

@pytest.mark.django_db
def test_pagination_is_reported_in_metadata(client, catalogue):
    config.SOPDS_MAXITEMS = 1
    body = feed(client, 'opds:opds2_new')

    assert body['metadata']['numberOfItems'] == 2
    assert body['metadata']['itemsPerPage'] == 1
    assert body['metadata']['currentPage'] == 1
    assert len(body['publications']) == 1


@pytest.mark.django_db
def test_next_and_previous_appear_only_where_they_lead_somewhere(client, catalogue):
    config.SOPDS_MAXITEMS = 1

    first = feed(client, 'opds:opds2_new')
    rels = {l['rel'] for l in first['links']}
    assert 'next' in rels and 'previous' not in rels

    second = feed(client, 'opds:opds2_new', page=2)
    rels = {l['rel'] for l in second['links']}
    assert 'previous' in rels and 'next' not in rels


@pytest.mark.django_db
def test_a_nonsense_page_does_not_error(client, catalogue):
    assert feed(client, 'opds:opds2_new', page='abc')['metadata']['currentPage'] == 1
    assert feed(client, 'opds:opds2_new', page='-5')['metadata']['currentPage'] == 1


@pytest.mark.django_db
def test_a_client_cannot_ask_for_the_whole_catalogue_at_once(client, catalogue):
    config.SOPDS_MAXITEMS = 100000
    assert feed(client, 'opds:opds2_new')['metadata']['itemsPerPage'] == opds2_max()


def opds2_max():
    from opds_catalog.opds2_views import MAX_PER_PAGE
    return MAX_PER_PAGE


# --- search ----------------------------------------------------------------

@pytest.mark.django_db
def test_search_finds_by_title(client, catalogue):
    body = feed(client, 'opds:opds2_search', query='sanctuary')
    assert [p['metadata']['title'] for p in body['publications']] == ['The Sanctuary Sparrow']


@pytest.mark.django_db
def test_an_empty_search_is_an_empty_feed_not_an_error(client, catalogue):
    """A client following the template before anything is typed gets this."""
    body = feed(client, 'opds:opds2_search')
    assert body['publications'] == []


# --- access ----------------------------------------------------------------

@pytest.mark.django_db
def test_the_feeds_honour_authentication(client, catalogue):
    config.SOPDS_AUTH = True
    assert client.get(reverse('opds:opds2_root')).status_code == 401
    assert client.get(reverse('opds:opds2_new')).status_code == 401


@pytest.mark.django_db
def test_basic_auth_is_accepted(client, catalogue, django_user_model):
    import base64
    config.SOPDS_AUTH = True
    django_user_model.objects.create_user(username='reader', password='pw')
    header = 'Basic %s' % base64.b64encode(b'reader:pw').decode()
    assert client.get(reverse('opds:opds2_root'), HTTP_AUTHORIZATION=header).status_code == 200


@pytest.mark.django_db
def test_browsing_is_not_rate_limited(client, catalogue):
    """Listing is cheap; it is handing out content that costs."""
    config.SOPDS_RATE_LIMIT = 2
    for _ in range(6):
        assert client.get(reverse('opds:opds2_new')).status_code == 200


# --- encoding --------------------------------------------------------------

@pytest.mark.django_db
def test_cyrillic_is_not_escaped_into_triple_the_bytes(client, catalogue):
    catalogue['second'].title = 'Понедельник начинается в субботу'
    catalogue['second'].save()

    raw = client.get(reverse('opds:opds2_new')).content.decode()
    assert 'Понедельник' in raw
    assert '\\u041f' not in raw
