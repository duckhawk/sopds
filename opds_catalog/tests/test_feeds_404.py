"""OPDS feeds must return 404 (not 500) for a non-existent object id.

GenresFeed looks up the Genre inside items(), which the Django syndication
framework does NOT wrap, so an unknown id raised DoesNotExist (HTTP 500) until
it was switched to get_object_or_404.

The SearchBooksFeed "doubles" lookup lives in get_object(), which Feed.__call__
already wraps in `except ObjectDoesNotExist -> Http404`, so it returns 404
without any change here; that test is a behavioural guard against the lookup
being moved out of get_object() later.
"""
import pytest

MISSING_ID = 999999


@pytest.fixture
def logged_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="opds", password="pw")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_genres_feed_missing_section_returns_404(logged_client):
    resp = logged_client.get(f"/opds/genres/{MISSING_ID}/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_search_doubles_feed_missing_book_returns_404(logged_client):
    resp = logged_client.get(f"/opds/search/books/d/{MISSING_ID}/")
    assert resp.status_code == 404
