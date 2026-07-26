"""Searching the catalogue by ISBN, in the web UI and over OPDS."""
import pytest
from django.urls import reverse
from constance import config

from opds_catalog.models import Book, Catalog

# Checksum-valid; normalize_isbn rejects anything else, so the tests cannot pass
# with a made-up number.
ISBN13 = '9780306406157'
ISBN10 = '0306406152'
OTHER_ISBN = '9783161484100'


@pytest.fixture
def catalogue(db):
    config.SOPDS_AUTH = True
    config.SOPDS_DOUBLES_HIDE = False
    config.SOPDS_MAXITEMS = 60
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)

    def add(title, isbn):
        return Book.objects.create(
            filename='%s.fb2' % title, path='.', filesize=1, format='fb2', cat_type=0,
            docdate='2011', lang='en', title=title, search_title=title.upper(),
            annotation='', avail=2, catalog=cat, isbn=isbn,
        )

    return {
        'hit': add('Book with an isbn', ISBN13),
        'other_edition': add('Same book other edition', ISBN13),
        'miss': add('Book with another isbn', OTHER_ISBN),
        'none': add('Book with no isbn', ''),
    }


@pytest.fixture
def client(client, django_user_model, db):
    user = django_user_model.objects.create_user(username='reader', password='pw')
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_opds_isbn_search_finds_every_edition(client, catalogue):
    body = client.get(reverse('opds:searchbooks',
                              kwargs={'searchtype': 'x', 'searchterms': ISBN13})).content.decode()
    assert 'Book with an isbn' in body
    assert 'Same book other edition' in body
    assert 'Book with another isbn' not in body


@pytest.mark.django_db
@pytest.mark.parametrize('term', ['978-0-306-40615-7', '978 0 306 40615 7', 'ISBN 9780306406157'])
def test_isbn_search_accepts_the_forms_people_paste(client, catalogue, term):
    """Hyphens, spaces and an ISBN prefix are all normalised away."""
    body = client.get(reverse('web:searchbooks'), {'searchtype': 'x', 'searchterms': term}).content.decode()
    assert 'Book with an isbn' in body


@pytest.mark.django_db
def test_an_isbn_that_is_not_one_matches_nothing(client, catalogue):
    """Not everything: the stored value is always normalised, so there is no
    sensible substring fallback."""
    body = client.get(reverse('web:searchbooks'), {'searchtype': 'x', 'searchterms': 'not an isbn'}).content.decode()
    assert 'Book with an isbn' not in body
    assert 'Book with no isbn' not in body


@pytest.mark.django_db
def test_isbn10_and_isbn13_are_distinct_terms(client, catalogue):
    """They are different strings and are stored as given; searching one does
    not silently return the other."""
    body = client.get(reverse('web:searchbooks'), {'searchtype': 'x', 'searchterms': ISBN10}).content.decode()
    assert 'Book with an isbn' not in body


@pytest.mark.django_db
def test_pasting_an_isbn_into_the_title_box_finds_the_book(client, catalogue):
    """Default search type is 'm' (title substring), which would find nothing."""
    body = client.get(reverse('web:searchbooks'), {'searchtype': 'm', 'searchterms': ISBN13}).content.decode()
    assert 'Book with an isbn' in body


@pytest.mark.django_db
def test_a_title_search_is_not_shadowed(client, catalogue):
    body = client.get(reverse('web:searchbooks'), {'searchtype': 'm', 'searchterms': 'Book with'}).content.decode()
    assert 'Book with an isbn' in body
    assert 'Book with no isbn' in body


@pytest.mark.django_db
def test_the_book_card_shows_and_links_the_isbn(client, catalogue):
    body = client.get(reverse('web:searchbooks'), {'searchtype': 'x', 'searchterms': ISBN13}).content.decode()
    assert ISBN13 in body
    assert 'searchtype=x&searchterms=%s' % ISBN13 in body


@pytest.mark.django_db
def test_opds_search_menu_offers_isbn_only_for_an_isbn(client, catalogue):
    as_isbn = client.get(reverse('opds:searchtypes', kwargs={'searchterms': ISBN13})).content.decode()
    assert 'Search by ISBN' in as_isbn

    as_title = client.get(reverse('opds:searchtypes', kwargs={'searchterms': 'Sparrow'})).content.decode()
    assert 'Search by ISBN' not in as_title


@pytest.mark.django_db
def test_opds_entry_reports_the_isbn(client, catalogue):
    body = client.get(reverse('opds:searchbooks',
                              kwargs={'searchtype': 'x', 'searchterms': ISBN13})).content.decode()
    assert 'ISBN: ' in body
