"""Title-substring ('m') search: the fenced pg_trgm page fetch must keep the
same ordering, paging and doubles-dedup behaviour."""
import pytest
from django.urls import reverse

from opds_catalog.models import Book, Catalog, Author, Series, bauthor
from opds_catalog.utils import contains_page_ids


@pytest.fixture
def catalog(db):
    return Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)


def _book(title, catalog, author=None):
    b = Book.objects.create(
        filename=f"{title}.fb2", path='.', filesize=1, format='fb2', cat_type=0,
        docdate='2016', lang='ru', title=title, search_title=title.upper(),
        annotation='', avail=2, catalog=catalog,
    )
    if author:
        bauthor.objects.create(book=b, author=author)
    return b


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="q", password="pw")


@pytest.fixture
def logged_client(client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_contains_page_ids_filters_orders_pages(catalog):
    ids = {t: _book(t, catalog).id for t in ['AAA', 'AAB', 'ABC', 'XYZ']}
    args = ('opds_catalog_book', 'search_title', 'A', 'search_title, docdate',
            'search_title, docdate DESC')
    # substring filter + ordering
    assert contains_page_ids(*args, 10, 0) == [ids['AAA'], ids['AAB'], ids['ABC']]
    # limit + offset
    assert contains_page_ids(*args, 2, 1) == [ids['AAB'], ids['ABC']]
    # non-matching term
    assert contains_page_ids('opds_catalog_book', 'search_title', 'ZZZ',
                             'search_title, docdate', 'search_title, docdate DESC', 10, 0) == []


@pytest.mark.django_db
def test_search_m_orders_and_dedups(logged_client, catalog):
    author = Author.objects.create(full_name='Иванов', search_full_name='ИВАНОВ')
    _book('РОМАН АЛЬФА', catalog, author)
    _book('РОМАН АЛЬФА', catalog, author)   # exact duplicate (title + author)
    _book('РОМАН БЕТА', catalog, author)
    resp = logged_client.get(reverse('web:searchbooks'),
                             {'searchtype': 'm', 'searchterms': 'РОМАН'})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'РОМАН АЛЬФА' in body and 'РОМАН БЕТА' in body
    # the duplicate collapsed into one entry carrying a doubles count
    assert 'шт.' in body


@pytest.mark.django_db
def test_search_authors_m(logged_client):
    for name in ['Пушкин', 'Пушков', 'Толстой']:
        Author.objects.create(full_name=name, search_full_name=name.upper())
    resp = logged_client.get(reverse('web:searchauthors'),
                             {'searchtype': 'm', 'searchterms': 'ПУШ'})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'Пушкин' in body and 'Пушков' in body
    assert 'Толстой' not in body


@pytest.mark.django_db
def test_search_series_m(logged_client):
    for s in ['Хоббит', 'Хоррор', 'Дюна']:
        Series.objects.create(ser=s, search_ser=s.upper())
    resp = logged_client.get(reverse('web:searchseries'),
                             {'searchtype': 'm', 'searchterms': 'ХО'})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'Хоббит' in body and 'Хоррор' in body
    assert 'Дюна' not in body
