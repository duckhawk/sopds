"""Tests for the htmx live-search suggestions endpoint (web:suggest)."""
import pytest
from django.urls import reverse

from opds_catalog.models import Book, Catalog, Author, Series


@pytest.fixture
def logged_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="sue", password="pw")
    client.force_login(user)
    return client


def _make_book(title):
    catalog = Catalog.objects.create(parent=None, cat_name=".", path=".", cat_type=0)
    return Book.objects.create(
        filename=f"{title}.fb2", path=".", filesize=1, format="fb2", cat_type=0,
        docdate="2016", lang="ru", title=title, search_title=title.upper(),
        annotation="", avail=2, catalog=catalog,
    )


@pytest.mark.django_db
def test_suggest_titles(logged_client):
    _make_book("Book One")
    _make_book("Book Two")
    _make_book("Other")
    resp = logged_client.post(
        reverse("web:suggest"), {"searchterms": "book", "suggesttype": "title"}
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Book One" in body and "Book Two" in body
    assert "Other" not in body
    assert "searchtype=i" in body  # title suggestions link to the single-book view


@pytest.mark.django_db
def test_suggest_authors(logged_client):
    Author.objects.create(full_name="Leo Tolstoy", search_full_name="LEO TOLSTOY")
    Author.objects.create(full_name="Mark Twain", search_full_name="MARK TWAIN")
    resp = logged_client.post(
        reverse("web:suggest"), {"searchterms": "tol", "suggesttype": "author"}
    )
    body = resp.content.decode()
    assert "Leo Tolstoy" in body and "Mark Twain" not in body
    assert "searchtype=a" in body


@pytest.mark.django_db
def test_suggest_series(logged_client):
    Series.objects.create(ser="Foundation", search_ser="FOUNDATION")
    Series.objects.create(ser="Dune", search_ser="DUNE")
    resp = logged_client.post(
        reverse("web:suggest"), {"searchterms": "found", "suggesttype": "series"}
    )
    body = resp.content.decode()
    assert "Foundation" in body and "Dune" not in body
    assert "searchtype=s" in body


@pytest.mark.django_db
def test_suggest_too_short_is_empty(logged_client):
    _make_book("Book One")
    resp = logged_client.post(
        reverse("web:suggest"), {"searchterms": "b", "suggesttype": "title"}
    )
    assert resp.status_code == 200
    assert "<li>" not in resp.content.decode()


@pytest.mark.django_db
def test_suggest_requires_auth_when_enabled(client):
    """With SOPDS_AUTH on (default), the endpoint is not open to anonymous users."""
    resp = client.post(
        reverse("web:suggest"), {"searchterms": "book", "suggesttype": "title"}
    )
    # @sopds_login redirects unauthenticated users to the login page.
    assert resp.status_code in (301, 302)
