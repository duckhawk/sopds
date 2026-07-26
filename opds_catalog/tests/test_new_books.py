"""The "recently added" listing: OPDS feed entry, ordering and the web page."""
import pytest
from django.urls import reverse
from django.utils import timezone
from constance import config

from opds_catalog.models import Book, Catalog, Counter


@pytest.fixture
def catalogue(db):
    config.SOPDS_AUTH = False
    config.SOPDS_DOUBLES_HIDE = False
    config.SOPDS_MAXITEMS = 60
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    now = timezone.now()
    # Registered oldest-first, so insertion order is the reverse of the expected
    # feed order — an accidental ordering by id would fail the test.
    for n, title in enumerate(['Oldest book', 'Middle book', 'Newest book']):
        Book.objects.create(
            filename='%s.fb2' % n, path='.', filesize=1, format='fb2', cat_type=0,
            docdate='2011', lang='en', title=title, search_title=title.upper(),
            annotation='', avail=2, catalog=cat,
            registerdate=now - timezone.timedelta(days=10 - n),
        )
    # The main feed disables the link on any entry whose counter is 0, so the
    # catalogue has to look scanned.
    Counter.objects.update_known_counters()
    return cat


@pytest.fixture
def web_client(client, django_user_model):
    user = django_user_model.objects.create_user(username='reader', password='pw')
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_main_feed_offers_recently_added(client, catalogue):
    body = client.get(reverse('opds:main')).content.decode()
    assert 'Recently added' in body
    assert reverse('opds:newbooks') in body


@pytest.mark.django_db
def test_newbooks_feed_is_ordered_newest_first(client, catalogue):
    resp = client.get(reverse('opds:newbooks'))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert body.index('Newest book') < body.index('Middle book') < body.index('Oldest book')


@pytest.mark.django_db
def test_newbooks_feed_paginates(client, catalogue):
    config.SOPDS_MAXITEMS = 2
    body = client.get(reverse('opds:newbooks')).content.decode()
    assert 'Newest book' in body
    assert 'Oldest book' not in body
    # The next-page link is built by SearchBooksFeed from the same searchtype.
    assert reverse('opds:searchbooks', kwargs={'searchtype': 'n', 'searchterms': '0', 'page': 2}) in body

    page2 = client.get(reverse('opds:searchbooks',
                               kwargs={'searchtype': 'n', 'searchterms': '0', 'page': 2}))
    assert 'Oldest book' in page2.content.decode()


@pytest.mark.django_db
def test_newbooks_feed_on_an_empty_catalogue(client, db):
    config.SOPDS_AUTH = False
    assert client.get(reverse('opds:newbooks')).status_code == 200


@pytest.mark.django_db
def test_web_page_is_ordered_newest_first(web_client, catalogue):
    resp = web_client.get(reverse('web:searchbooks'), {'searchtype': 'n'})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert body.index('Newest book') < body.index('Middle book') < body.index('Oldest book')


@pytest.mark.django_db
def test_web_nav_links_to_recently_added(web_client, catalogue):
    body = web_client.get(reverse('web:main')).content.decode()
    assert 'searchtype=n' in body
