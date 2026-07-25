"""Non-numeric ids in search/genre params must return 404, not 500.

Several views cast a user-supplied value to int() with no guard, so a
non-numeric value raised ValueError -> HTTP 500. Covers:
 - web GenresView (?section=abc)
 - web SearchBooksView doubles (searchtype=d, non-numeric searchterms)
 - OPDS SearchBooksFeed doubles (/opds/search/books/d/<non-numeric>/)
"""
import pytest
from django.urls import reverse


@pytest.fixture
def logged_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="mel", password="pw")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_genres_view_non_numeric_section_returns_404(logged_client):
    resp = logged_client.get(reverse("web:genre"), {"section": "abc"})
    assert resp.status_code == 404


@pytest.mark.django_db
def test_search_doubles_non_numeric_returns_404(logged_client):
    resp = logged_client.get(
        reverse("web:searchbooks"), {"searchtype": "d", "searchterms": "abc"}
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_opds_search_doubles_non_numeric_returns_404(logged_client):
    resp = logged_client.get("/opds/search/books/d/abc/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_search_by_id_missing_book_does_not_500(logged_client):
    # searchtype='i' with a non-existent book id used to do books[0].title on an
    # empty queryset -> IndexError -> HTTP 500. It must render normally instead.
    resp = logged_client.get(
        reverse("web:searchbooks"), {"searchtype": "i", "searchterms": "999999"}
    )
    assert resp.status_code == 200
