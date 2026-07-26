"""A request for a non-existent book must return 404, not 500.

The download/convert/read/cover views used Book.objects.get(id=...), which
raised DoesNotExist (HTTP 500) for an unknown id. They now use
get_object_or_404.
"""
import pytest
from django.urls import reverse
from constance import config

MISSING_ID = 999999


@pytest.fixture
def signed_in(client, django_user_model, db):
    # These routes serve catalogue content and answer 401 before they ever look
    # the book up, so the caller has to be authenticated for a 404 to be what
    # the test is actually measuring.
    config.SOPDS_AUTH = True
    client.force_login(django_user_model.objects.create_user(username='dl404', password='pw'))
    return client


@pytest.mark.django_db
@pytest.mark.parametrize("name,args", [
    ("opds:download", [MISSING_ID, 0]),
    ("opds:convert", [MISSING_ID, "epub"]),
    ("opds:read", [MISSING_ID]),
    ("opds:cover", [MISSING_ID]),
])
def test_missing_book_returns_404(signed_in, name, args):
    resp = signed_in.get(reverse(name, args=args))
    assert resp.status_code == 404
