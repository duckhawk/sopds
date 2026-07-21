"""Regression tests locking in that alphabet_menu is SQL-injection safe.

alphabet_menu() builds a GROUP BY over user-controlled `chars`/`lang_code`.
These must be passed as bound parameters (not string-interpolated), and LIKE
wildcards must be escaped. The upstream project once interpolated them into
raw SQL; these tests make sure we never regress to that.
"""
import pytest

from opds_catalog.models import Book, Catalog
from opds_catalog.utils import alphabet_menu

TABLE = "opds_catalog_book"
COLUMN = "search_title"


def _make_book(title):
    catalog = Catalog.objects.create(parent=None, cat_name=".", path=".", cat_type=0)
    return Book.objects.create(
        filename=f"{title}.fb2", path=".", filesize=1, format="fb2", cat_type=0,
        docdate="2016", lang="ru", title=title, search_title=title.upper(),
        annotation="", avail=2, catalog=catalog,
    )


@pytest.mark.django_db
def test_alphabet_menu_counts_normal_prefix():
    _make_book("Alpha")
    _make_book("Andrew")
    _make_book("Boris")
    result = alphabet_menu(TABLE, COLUMN, "", "A")
    counts = {row.id: row.cnt for row in result}
    # Two titles start with "A" -> both share the 2-char prefix "AN"? no:
    # "ALPHA" and "ANDREW" -> prefixes "AL" and "AN", one each.
    assert counts == {"AL": 1, "AN": 1}


# The payload is intentionally used as (part of) a cache key, which Django's
# cache layer flags as memcached-incompatible. That warning is noise here (the
# project uses locmem/redis, not memcached), so silence it for this test only.
@pytest.mark.filterwarnings(
    "ignore::django.core.cache.backends.base.CacheKeyWarning"
)
@pytest.mark.django_db
def test_alphabet_menu_rejects_sql_injection():
    _make_book("Alpha")
    before = Book.objects.count()

    payload = "'; DROP TABLE opds_catalog_book; --"
    # Must not raise, must not execute the injected statement.
    result = alphabet_menu(TABLE, COLUMN, payload, payload)

    assert result == []
    # The table is still there and untouched -> the payload was treated as data.
    assert Book.objects.count() == before


@pytest.mark.django_db
def test_alphabet_menu_escapes_like_wildcards():
    """A literal % in the prefix must match literally, not as a wildcard."""
    _make_book("Nine")           # search_title "NINE"
    _make_book("%discount")      # search_title "%DISCOUNT"
    result = alphabet_menu(TABLE, COLUMN, "", "%")
    counts = {row.id: row.cnt for row in result}
    # Only the title literally starting with "%" matches; "NINE" must not.
    assert counts == {"%D": 1}
