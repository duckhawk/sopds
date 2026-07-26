"""Community rating aggregation: the helper, the listing, the feed and the page."""
import pytest
from django.urls import reverse
from constance import config

from opds_catalog import ratings
from opds_catalog.models import Book, Catalog, Counter, bookshelf


@pytest.fixture
def library(db, django_user_model):
    config.SOPDS_AUTH = True
    config.SOPDS_DOUBLES_HIDE = False
    config.SOPDS_MAXITEMS = 60
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)

    def add(title):
        return Book.objects.create(
            filename='%s.fb2' % title, path='.', filesize=1, format='fb2', cat_type=0,
            docdate='2011', lang='en', title=title, search_title=title.upper(),
            annotation='', avail=2, catalog=cat)

    books = {n: add(n) for n in ('Great book', 'Fine book', 'Dull book', 'Unrated book')}
    users = [django_user_model.objects.create_user(username='u%d' % i, password='pw')
             for i in range(3)]

    def rate(book, *stars):
        for user, n in zip(users, stars):
            bookshelf.objects.update_or_create(user=user, book=book, defaults={'rating': n})

    rate(books['Great book'], 5, 5, 4)     # avg 4.7, 3 votes
    rate(books['Fine book'], 4)            # avg 4.0, 1 vote
    rate(books['Dull book'], 2, 1)         # avg 1.5, 2 votes
    Counter.objects.update_known_counters()
    return {'books': books, 'users': users}


@pytest.fixture
def signed_in(client, library):
    client.force_login(library['users'][0])
    return client


# --- the helper ------------------------------------------------------------

@pytest.mark.django_db
def test_summary_averages_across_users(library):
    got = ratings.summary([b.id for b in library['books'].values()])
    assert got[library['books']['Great book'].id] == {'average': 4.7, 'votes': 3}
    assert got[library['books']['Dull book'].id] == {'average': 1.5, 'votes': 2}


@pytest.mark.django_db
def test_summary_omits_unrated_books(library):
    """Absent, not zero — so a caller can tell "unrated" from "rated badly"."""
    got = ratings.summary([b.id for b in library['books'].values()])
    assert library['books']['Unrated book'].id not in got


@pytest.mark.django_db
def test_summary_ignores_shelf_rows_without_a_rating(library):
    """A book on the shelf that was never rated must not count as a vote."""
    book = library['books']['Fine book']
    bookshelf.objects.create(user=library['users'][2], book=book)   # rating stays None
    assert ratings.summary([book.id])[book.id] == {'average': 4.0, 'votes': 1}


@pytest.mark.django_db
def test_summary_of_nothing_is_empty(library):
    assert ratings.summary([]) == {}


@pytest.mark.django_db
def test_summary_costs_one_query(library, django_assert_num_queries):
    ids = [b.id for b in library['books'].values()]
    with django_assert_num_queries(1):
        ratings.summary(ids)


# --- ordering --------------------------------------------------------------

@pytest.mark.django_db
def test_top_rated_is_best_first_and_excludes_unrated(library):
    titles = [b.title for b in ratings.top_rated()]
    assert titles == ['Great book', 'Fine book', 'Dull book']


@pytest.mark.django_db
def test_votes_break_a_tie_on_the_average(library, django_user_model):
    """A 5 from three people should outrank a 5 from one."""
    lone = library['books']['Unrated book']
    bookshelf.objects.create(user=library['users'][0], book=lone, rating=5)
    popular = library['books']['Great book']
    for i, user in enumerate(library['users']):
        bookshelf.objects.update_or_create(user=user, book=popular, defaults={'rating': 5})

    titles = [b.title for b in ratings.top_rated()]
    assert titles.index('Great book') < titles.index('Unrated book')


# --- OPDS ------------------------------------------------------------------

@pytest.mark.django_db
def test_root_feed_offers_top_rated(signed_in, library):
    body = signed_in.get(reverse('opds:main')).content.decode()
    assert 'Top rated' in body
    assert reverse('opds:toprated') in body


@pytest.mark.django_db
def test_top_rated_feed_is_ordered(signed_in, library):
    body = signed_in.get(reverse('opds:toprated')).content.decode()
    assert body.index('Great book') < body.index('Fine book') < body.index('Dull book')
    assert 'Unrated book' not in body


@pytest.mark.django_db
def test_feed_entry_reports_the_average(signed_in, library):
    body = signed_in.get(reverse('opds:toprated')).content.decode()
    assert '4.7/5 (3)' in body


# --- web -------------------------------------------------------------------

@pytest.mark.django_db
def test_web_page_is_ordered_by_rating(signed_in, library):
    resp = signed_in.get(reverse('web:searchbooks'), {'searchtype': 'r'})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert body.index('Great book') < body.index('Fine book') < body.index('Dull book')


@pytest.mark.django_db
def test_book_card_shows_the_average_next_to_your_own_stars(signed_in, library):
    body = signed_in.get(reverse('web:searchbooks'),
                         {'searchtype': 'm', 'searchterms': 'GREAT'}).content.decode()
    assert '4.7' in body
    assert 'data-rating="5"' in body      # u0 rated it 5


@pytest.mark.django_db
def test_an_unrated_book_shows_no_average(signed_in, library):
    body = signed_in.get(reverse('web:searchbooks'),
                         {'searchtype': 'm', 'searchterms': 'UNRATED'}).content.decode()
    assert 'Unrated book' in body
    assert '&#9733;&nbsp;' not in body


@pytest.mark.django_db
def test_nav_links_to_top_rated(signed_in, library):
    body = signed_in.get(reverse('web:main')).content.decode()
    assert 'searchtype=r' in body


@pytest.mark.django_db
def test_changing_your_own_rating_replaces_your_vote(signed_in, library):
    """u0 is the one who rated 'Fine book' a 4, so this is not a second vote."""
    book = library['books']['Fine book']
    assert ratings.summary([book.id])[book.id] == {'average': 4.0, 'votes': 1}

    assert signed_in.post(reverse('web:bsrating', args=[book.id]), {'rating': '2'}).status_code == 200
    assert ratings.summary([book.id])[book.id] == {'average': 2.0, 'votes': 1}


@pytest.mark.django_db
def test_a_new_voter_moves_the_average(client, library):
    book = library['books']['Fine book']          # u0 rated it 4
    client.force_login(library['users'][1])

    assert client.post(reverse('web:bsrating', args=[book.id]), {'rating': '2'}).status_code == 200
    assert ratings.summary([book.id])[book.id] == {'average': 3.0, 'votes': 2}


@pytest.mark.django_db
def test_clearing_a_rating_removes_the_vote(signed_in, library):
    book = library['books']['Great book']         # 5, 5, 4 from three users
    assert signed_in.post(reverse('web:bsrating', args=[book.id]), {'rating': '0'}).status_code == 200
    assert ratings.summary([book.id])[book.id] == {'average': 4.5, 'votes': 2}


# --- cost ------------------------------------------------------------------

@pytest.mark.django_db
def test_the_listing_does_not_touch_unrated_books(library, django_user_model):
    """The property the query is shaped around.

    Aggregating straight over Book made the join and GROUP BY cover the whole
    catalogue and then throw almost all of it away, so the cost grew with the
    number of books rather than the number of ratings — 4 ms at 2k books and
    66 ms at 60k, for a result that never changed. Restricting the outer query
    to books someone has rated is what fixes that, and this asserts the shape
    rather than a timing, which would be flaky.
    """
    cat = Catalog.objects.get()
    Book.objects.bulk_create([
        Book(filename='bulk%d.fb2' % n, path='.', filesize=1, format='fb2',
             cat_type=0, docdate='2011', lang='en', title='Bulk %d' % n,
             search_title='BULK %d' % n, annotation='', avail=2, catalog=cat)
        for n in range(500)])

    sql = str(ratings.top_rated().query)
    # The rated set is selected as a subquery the outer query filters on, rather
    # than every book being aggregated and then filtered by HAVING.
    assert 'IN (SELECT' in sql.upper()
    assert 'HAVING' not in sql.upper()

    # And the answer is still only the rated books.
    assert len(list(ratings.top_rated())) == 3
