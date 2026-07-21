"""BSDelView: htmx (hx-delete) returns 204 + HX-Redirect; the plain path still
redirects. Both remove the book from the user's shelf."""
import pytest
from django.urls import reverse

from opds_catalog.models import Book, Catalog, bookshelf


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="shelf", password="pw")


@pytest.fixture
def logged_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def shelved_book(user):
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    book = Book.objects.create(
        filename='b.fb2', path='.', filesize=1, format='fb2', cat_type=0,
        docdate='2016', lang='ru', title='Book', search_title='BOOK',
        annotation='', avail=2, catalog=cat,
    )
    bookshelf.objects.create(user=user, book=book)
    return book


@pytest.mark.django_db
def test_bsdel_htmx_returns_hx_redirect(logged_client, user, shelved_book):
    resp = logged_client.delete(
        reverse('web:bsdel') + f'?book={shelved_book.id}',
        HTTP_HX_REQUEST='true',
        HTTP_REFERER='http://testserver/web/search/books/?searchtype=u',
    )
    assert resp.status_code == 204
    assert resp['HX-Redirect'].endswith('searchtype=u')
    assert not bookshelf.objects.filter(user=user, book=shelved_book).exists()


@pytest.mark.django_db
def test_bsdel_plain_redirects(logged_client, user, shelved_book):
    resp = logged_client.get(
        reverse('web:bsdel'), {'book': shelved_book.id},
        HTTP_REFERER='http://testserver/web/',
    )
    assert resp.status_code == 302
    assert not bookshelf.objects.filter(user=user, book=shelved_book).exists()


@pytest.mark.django_db
def test_bsdel_non_numeric_book_is_noop(logged_client):
    resp = logged_client.get(reverse('web:bsdel'), {'book': 'abc'},
                             HTTP_REFERER='http://testserver/web/')
    assert resp.status_code == 302  # no 500
