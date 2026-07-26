"""Finding a genre by name, in OPDS and in the web UI.

Genres were browsable through the section tree but not searchable: reaching a
leaf like "Detective" meant knowing which section it sits under.
"""
import re

import pytest
from django.urls import reverse
from constance import config

from opds_catalog.models import Book, Catalog, Genre, bgenre


@pytest.fixture
def genres(db, django_user_model, client):
    config.SOPDS_AUTH = True
    config.SOPDS_MAXITEMS = 60
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)

    def add(section, subsection, books=1):
        genre = Genre.objects.create(genre=subsection.lower(), section=section,
                                     subsection=subsection)
        for n in range(books):
            book = Book.objects.create(
                filename='%s%d.fb2' % (subsection, n), path='.', filesize=1,
                format='fb2', cat_type=0, docdate='2011', lang='en',
                title='%s book %d' % (subsection, n),
                search_title=('%s BOOK %d' % (subsection, n)).upper(),
                annotation='', avail=2, catalog=cat)
            bgenre.objects.create(book=book, genre=genre)
        return genre

    made = {
        'Detective': add('Prose', 'Detective', 2),
        'Detective story': add('Prose', 'Detective story'),
        'Science fiction': add('Fantasy', 'Science fiction'),
        'Empty genre': add('Prose', 'Empty genre', books=0),
    }
    client.force_login(django_user_model.objects.create_user(username='r', password='pw'))
    return made


# --- OPDS ------------------------------------------------------------------

@pytest.mark.django_db
def test_opds_substring_match(client, genres):
    body = client.get(reverse('opds:searchgenres',
                              kwargs={'searchtype': 'm', 'searchterms': 'detect'})).content.decode()
    assert 'Detective' in body and 'Detective story' in body
    assert 'Science fiction' not in body


@pytest.mark.django_db
def test_opds_match_is_case_insensitive(client, genres):
    body = client.get(reverse('opds:searchgenres',
                              kwargs={'searchtype': 'm', 'searchterms': 'DETECTIVE'})).content.decode()
    assert 'Detective' in body


@pytest.mark.django_db
def test_opds_exact_and_prefix_modes(client, genres):
    exact = client.get(reverse('opds:searchgenres',
                               kwargs={'searchtype': 'e', 'searchterms': 'Detective'})).content.decode()
    assert 'Detective story' not in exact

    prefix = client.get(reverse('opds:searchgenres',
                                kwargs={'searchtype': 'b', 'searchterms': 'Detect'})).content.decode()
    assert 'Detective story' in prefix


@pytest.mark.django_db
def test_opds_entry_links_to_the_books_in_that_genre(client, genres):
    body = client.get(reverse('opds:searchgenres',
                              kwargs={'searchtype': 'm', 'searchterms': 'detective'})).content.decode()
    wanted = reverse('opds:searchbooks',
                     kwargs={'searchtype': 'g', 'searchterms': genres['Detective'].id})
    assert wanted in body


@pytest.mark.django_db
def test_opds_entry_reports_the_book_count(client, genres):
    body = client.get(reverse('opds:searchgenres',
                              kwargs={'searchtype': 'e', 'searchterms': 'Detective'})).content.decode()
    assert 'Books count: 2' in body


@pytest.mark.django_db
def test_opds_search_menu_offers_genres(client, genres):
    body = client.get(reverse('opds:searchtypes', kwargs={'searchterms': 'detective'})).content.decode()
    assert 'Search by genre' in body
    assert reverse('opds:searchgenres',
                   kwargs={'searchtype': 'm', 'searchterms': 'detective'}) in body


@pytest.mark.django_db
def test_opds_no_match_is_an_empty_feed_not_an_error(client, genres):
    resp = client.get(reverse('opds:searchgenres',
                              kwargs={'searchtype': 'm', 'searchterms': 'zzzz'}))
    assert resp.status_code == 200


# --- web -------------------------------------------------------------------

def listed(client, **params):
    body = client.get(reverse('web:searchgenres'), params).content.decode()
    return set(re.findall(r'searchtype=g&searchterms=(\d+)', body))


@pytest.mark.django_db
def test_web_search_finds_matching_genres(client, genres):
    found = listed(client, searchtype='m', searchterms='detect')
    assert found == {str(genres['Detective'].id), str(genres['Detective story'].id)}


@pytest.mark.django_db
def test_web_search_hides_genres_with_no_books(client, genres):
    """Consistent with the section browser, which already filters them out."""
    assert listed(client, searchtype='m', searchterms='empty') == set()


@pytest.mark.django_db
def test_web_result_names_the_section_for_context(client, genres):
    body = client.get(reverse('web:searchgenres'),
                      {'searchtype': 'm', 'searchterms': 'detect'}).content.decode()
    assert 'Prose' in body


@pytest.mark.django_db
def test_web_nav_offers_the_genre_search_type(client, genres):
    body = client.get(reverse('web:main')).content.decode()
    assert reverse('web:searchgenres') in body
    assert 'id="genre"' in body


@pytest.mark.django_db
def test_web_search_with_no_terms_does_not_error(client, genres):
    assert client.get(reverse('web:searchgenres')).status_code == 200
