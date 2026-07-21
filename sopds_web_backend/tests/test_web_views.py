"""Coverage for the web (HTML) views: browse pages, search, settings, theme
and the per-user bookshelf/status/rating/position actions.

Uses the shared testdb.json fixture (books/authors/genres/catalogs) and a
logged-in user so the SOPDS_AUTH code paths (bookshelf prefetch, etc.) run.
"""
import pytest
from django.urls import reverse

from opds_catalog.models import Book, Author, Genre, bookshelf, Theme


@pytest.fixture
def sample_library(db):
    from django.core.management import call_command
    call_command('loaddata', 'testdb.json', verbosity=0)


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="viewer", password="pw")


@pytest.fixture
def logged_client(client, user):
    client.force_login(user)
    return client


# --- browse / list pages ---------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("path", [
    "/web/",            # hello / main
    "/web/book/",       # BooksView (alphabet)
    "/web/catalog/",    # CatalogsView (root)
    "/web/author/",     # AuthorsView
    "/web/series/",     # SeriesView
    "/web/genre/",      # GenresView (section list)
])
def test_browse_pages_render(logged_client, sample_library, path):
    assert logged_client.get(path).status_code == 200


@pytest.mark.django_db
def test_genre_section_page(logged_client, sample_library):
    gid = Genre.objects.first().id
    assert logged_client.get(reverse("web:genre"), {"section": gid}).status_code == 200


# --- search views ----------------------------------------------------------

@pytest.mark.django_db
def test_search_books_all_types(logged_client, sample_library):
    aid = Author.objects.first().id
    gid = Genre.objects.first().id
    bid = Book.objects.first().id
    url = reverse("web:searchbooks")
    for params in [
        {"searchtype": "m", "searchterms": "a"},   # by title substring
        {"searchtype": "b", "searchterms": "THE"}, # by title prefix
        {"searchtype": "a", "searchterms": aid},   # by author
        {"searchtype": "s", "searchterms": 1},     # by series (none -> empty)
        {"searchtype": "g", "searchterms": gid},   # by genre
        {"searchtype": "i", "searchterms": bid},   # single book by id
        {"searchtype": "d", "searchterms": bid},   # doubles
    ]:
        resp = logged_client.get(url, params)
        assert resp.status_code == 200, params


@pytest.mark.django_db
def test_search_authors_and_series(logged_client, sample_library):
    assert logged_client.get(reverse("web:searchauthors"), {"searchtype": "m", "searchterms": "a"}).status_code == 200
    assert logged_client.get(reverse("web:searchseries"), {"searchtype": "m", "searchterms": "a"}).status_code == 200


# --- settings / theme ------------------------------------------------------

@pytest.mark.django_db
def test_settings_get_and_post(logged_client, user):
    assert logged_client.get(reverse("web:settings")).status_code == 200
    resp = logged_client.post(reverse("web:settings"),
                              {"theme": "dark", "reader_mode": Theme.READER_CHAPTERS, "font_size": "120"})
    assert resp.status_code == 302
    prefs = Theme.objects.get(user=user)
    assert prefs.theme_css == "css/sopds-dark.css"
    assert prefs.reader_mode == Theme.READER_CHAPTERS
    assert prefs.font_size == 120


@pytest.mark.django_db
def test_settings_post_clamps_font_size(logged_client, user):
    logged_client.post(reverse("web:settings"), {"font_size": "9999"})
    assert Theme.objects.get(user=user).font_size == 200


@pytest.mark.django_db
def test_theme_toggle_creates_dark(logged_client, user):
    resp = logged_client.get(reverse("web:theme"), HTTP_REFERER="http://testserver/web/")
    assert resp.status_code == 302
    assert Theme.objects.get(user=user).theme_css == "css/sopds-dark.css"


# --- bookshelf / status / rating / position --------------------------------

@pytest.mark.django_db
def test_bookshelf_add_status_rating_pos_clear(logged_client, user, sample_library):
    bid = Book.objects.first().id

    resp = logged_client.get(reverse("web:bsadd"), {"book": bid}, HTTP_REFERER="http://testserver/web/book/")
    assert resp.status_code == 302
    assert bookshelf.objects.filter(user=user, book_id=bid).exists()

    status = bookshelf.STATUS_CHOICES[1][0]  # first real status
    resp = logged_client.post(reverse("web:bsstatus", args=[bid]), {"status": status})
    assert resp.json()["ok"] is True
    assert bookshelf.objects.get(user=user, book_id=bid).status == status

    resp = logged_client.post(reverse("web:bsrating", args=[bid]), {"rating": "4"})
    assert resp.json()["ok"] is True
    assert bookshelf.objects.get(user=user, book_id=bid).rating == 4

    logged_client.get(reverse("web:setpos", args=[bid]), {"pos": "2.13"})
    assert logged_client.get(reverse("web:getpos", args=[bid])).content.decode() == "2.13"

    resp = logged_client.get(reverse("web:bsclear"))
    assert resp.status_code == 302
    assert not bookshelf.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_bsdel_removes_book(logged_client, user, sample_library):
    bid = Book.objects.first().id
    bookshelf.objects.create(user=user, book_id=bid)
    resp = logged_client.get(reverse("web:bsdel"), {"book": bid}, HTTP_REFERER="http://testserver/web/")
    assert resp.status_code == 302
    assert not bookshelf.objects.filter(user=user, book_id=bid).exists()


@pytest.mark.django_db
def test_bsrating_rejects_out_of_range(logged_client, sample_library):
    bid = Book.objects.first().id
    resp = logged_client.post(reverse("web:bsrating", args=[bid]), {"rating": "9"})
    assert resp.json()["ok"] is False


@pytest.mark.django_db
def test_bsstatus_rejects_invalid(logged_client, sample_library):
    bid = Book.objects.first().id
    resp = logged_client.post(reverse("web:bsstatus", args=[bid]), {"status": "bogus"})
    assert resp.json()["ok"] is False
