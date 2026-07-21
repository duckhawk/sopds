"""Web views must return 404 (not 500) for a non-existent object id.

GenresView and the "doubles" branch of SearchBooksView fetched Genre/Book by
id with .get(), raising DoesNotExist (HTTP 500) for an unknown id. They now
use get_object_or_404.
"""
import pytest

MISSING_ID = 999999


@pytest.fixture
def logged_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="bob", password="pw")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_genres_view_missing_section_returns_404(logged_client):
    from django.urls import reverse
    resp = logged_client.get(reverse("web:genre"), {"section": MISSING_ID})
    assert resp.status_code == 404


@pytest.mark.django_db
def test_search_doubles_missing_book_returns_404(logged_client):
    from django.urls import reverse
    resp = logged_client.get(
        reverse("web:searchbooks"), {"searchtype": "d", "searchterms": MISSING_ID}
    )
    assert resp.status_code == 404
