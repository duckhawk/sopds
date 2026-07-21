"""Non-numeric page/lang GET params must degrade gracefully (200), not 500.

int(request.GET[...]) ran unguarded for `page` (search/catalog views) and
`lang` (book/author/series browse), so a non-numeric value raised ValueError.
"""
import pytest
from django.urls import reverse


@pytest.fixture
def logged_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="param", password="pw")
    client.force_login(user)
    return client


@pytest.mark.django_db
@pytest.mark.parametrize("name,params", [
    ("web:book", {"lang": "abc"}),
    ("web:author", {"lang": "abc"}),
    ("web:series", {"lang": "abc"}),
    ("web:catalog", {"page": "abc"}),
    ("web:searchbooks", {"searchtype": "m", "searchterms": "x", "page": "abc"}),
    ("web:searchauthors", {"searchtype": "m", "searchterms": "x", "page": "abc"}),
    ("web:searchseries", {"searchtype": "m", "searchterms": "x", "page": "abc"}),
])
def test_non_numeric_params_do_not_500(logged_client, name, params):
    resp = logged_client.get(reverse(name), params)
    assert resp.status_code == 200
