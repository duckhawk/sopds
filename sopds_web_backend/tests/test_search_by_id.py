"""search-by-id (`searchtype=i`) must degrade gracefully for a missing id.

Regression guard for the old `books[0].title` IndexError -> 500: an unknown
book id now renders an empty result page (HTTP 200), not a 500.
"""
import pytest
from django.urls import reverse


@pytest.fixture
def logged_client(client, django_user_model):
    user = django_user_model.objects.create_user("sid", "s@x.y", "pw")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_search_by_id_missing_book_renders_200(logged_client):
    resp = logged_client.get(reverse("web:searchbooks"), {"searchtype": "i", "searchterms": 999999})
    assert resp.status_code == 200


@pytest.mark.django_db
def test_search_by_id_non_numeric_renders_200(logged_client):
    resp = logged_client.get(reverse("web:searchbooks"), {"searchtype": "i", "searchterms": "abc"})
    assert resp.status_code == 200
