"""The convert route must only accept the known conversion targets.

Before hardening, convert_type was matched by a permissive `.+` regex and an
unknown value left `converter_path` unbound in ConvertFB2 -> UnboundLocalError
(HTTP 500). The URL now restricts convert_type to epub|mobi so anything else
does not resolve at all.
"""
import pytest
from django.urls import resolve
from django.urls.exceptions import Resolver404


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["epub", "mobi"])
def test_convert_url_accepts_known_types(fmt):
    match = resolve(f"/opds/convert/5/{fmt}/")
    assert match.url_name == "convert"
    assert match.kwargs["convert_type"] == fmt
    assert match.kwargs["book_id"] == "5"


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["pdf", "djvu", "txt", "fb2", "epub.sh"])
def test_convert_url_rejects_unknown_types(fmt):
    with pytest.raises(Resolver404):
        resolve(f"/opds/convert/5/{fmt}/")
