"""A request for a non-existent book must return 404, not 500.

The download/convert/read/cover views used Book.objects.get(id=...), which
raised DoesNotExist (HTTP 500) for an unknown id. They now use
get_object_or_404.
"""
import pytest
from django.urls import reverse

MISSING_ID = 999999


@pytest.mark.django_db
@pytest.mark.parametrize("name,args", [
    ("opds:download", [MISSING_ID, 0]),
    ("opds:convert", [MISSING_ID, "epub"]),
    ("opds:read", [MISSING_ID]),
    ("opds:cover", [MISSING_ID]),
])
def test_missing_book_returns_404(client, name, args):
    resp = client.get(reverse(name, args=args))
    assert resp.status_code == 404
