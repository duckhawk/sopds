"""Named lists of books belonging to a reader.

Distinct from the two neighbours on purpose: the bookshelf is an automatic
record of what you opened, tags are shared metadata about the book, and a
collection is a deliberate grouping that means something only to its owner.
"""
import pytest
from django.urls import reverse
from constance import config

from opds_catalog import collections
from opds_catalog.models import Book, Catalog, Collection, CollectionBook, Counter


@pytest.fixture
def library(db, django_user_model, client):
    config.SOPDS_AUTH = True
    config.SOPDS_DOUBLES_HIDE = False
    config.SOPDS_MAXITEMS = 60
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)

    def add(title):
        return Book.objects.create(
            filename='%s.fb2' % title, path='.', filesize=1, format='fb2', cat_type=0,
            docdate='2011', lang='en', title=title, search_title=title.upper(),
            annotation='', avail=2, catalog=cat)

    owner = django_user_model.objects.create_user(username='owner', password='pw')
    other = django_user_model.objects.create_user(username='other', password='pw')
    client.force_login(owner)
    Counter.objects.update_known_counters()
    return {'first': add('First book'), 'second': add('Second book'),
            'owner': owner, 'other': other}


def create(client, name, shared=None):
    data = {'name': name}
    if shared is not None:
        data['shared'] = '1' if shared else '0'
    return client.post(reverse('web:collection_create'), data)


# --- making lists ----------------------------------------------------------

@pytest.mark.django_db
def test_a_reader_can_make_a_list(client, library):
    resp = create(client, 'For the holiday')
    assert resp.status_code == 200 and resp.json()['ok']
    assert Collection.objects.get().name == 'For the holiday'


@pytest.mark.django_db
def test_lists_are_private_unless_shared(client, library):
    create(client, 'Private')
    assert Collection.objects.get().shared is False


@pytest.mark.django_db
def test_two_readers_may_use_the_same_name(client, library, django_user_model):
    create(client, 'Favourites')
    client.force_login(library['other'])
    create(client, 'Favourites')
    assert Collection.objects.filter(name='Favourites').count() == 2


@pytest.mark.django_db
def test_one_reader_may_not(client, library):
    create(client, 'Favourites')
    create(client, 'Favourites')
    assert Collection.objects.filter(user=library['owner']).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize('name', ['', '   '])
def test_an_empty_name_is_refused(client, library, name):
    assert create(client, name).status_code == 400
    assert not Collection.objects.exists()


@pytest.mark.django_db
def test_the_number_of_lists_is_capped(client, library):
    for n in range(collections.MAX_PER_USER):
        collections.create(library['owner'], 'list %d' % n)
    assert create(client, 'one too many').status_code == 400


# --- putting books in ------------------------------------------------------

@pytest.mark.django_db
def test_a_book_can_be_added_and_removed(client, library):
    collection = collections.create(library['owner'], 'Mine')
    book = library['first']

    add_url = reverse('web:collection_add', args=[collection.id, book.id])
    assert client.post(add_url).json()['added'] is True
    assert list(collections.books_in(collection)) == [book]

    remove_url = reverse('web:collection_remove', args=[collection.id, book.id])
    assert client.post(remove_url).json()['removed'] is True
    assert not collections.books_in(collection).exists()


@pytest.mark.django_db
def test_adding_twice_is_not_a_duplicate(client, library):
    collection = collections.create(library['owner'], 'Mine')
    url = reverse('web:collection_add', args=[collection.id, library['first'].id])
    client.post(url)
    assert client.post(url).json()['added'] is False
    assert CollectionBook.objects.count() == 1


@pytest.mark.django_db
def test_books_keep_the_order_they_were_added(client, library):
    collection = collections.create(library['owner'], 'Ordered')
    client.post(reverse('web:collection_add', args=[collection.id, library['second'].id]))
    client.post(reverse('web:collection_add', args=[collection.id, library['first'].id]))

    assert [b.title for b in collections.books_in(collection)] == ['Second book', 'First book']


@pytest.mark.django_db
def test_deleting_a_list_does_not_delete_its_books(client, library):
    collection = collections.create(library['owner'], 'Doomed')
    collections.add_book(collection, library['first'])

    client.post(reverse('web:collection_delete', args=[collection.id]))
    assert not Collection.objects.exists()
    assert Book.objects.filter(pk=library['first'].pk).exists()


# --- who may see and change what -------------------------------------------

@pytest.mark.django_db
def test_a_private_list_is_invisible_to_others(client, library):
    collection = collections.create(library['owner'], 'Private')
    client.force_login(library['other'])

    assert collection not in collections.visible(library['other'])
    resp = client.get(reverse('web:searchbooks'),
                      {'searchtype': 'c', 'searchterms': collection.id})
    # 404 rather than 403: its existence is not confirmed either.
    assert resp.status_code == 404


@pytest.mark.django_db
def test_a_shared_list_is_visible_to_everyone(client, library):
    collection = collections.create(library['owner'], 'Shared', shared=True)
    collections.add_book(collection, library['first'])
    client.force_login(library['other'])

    body = client.get(reverse('web:searchbooks'),
                      {'searchtype': 'c', 'searchterms': collection.id}).content.decode()
    assert 'First book' in body


@pytest.mark.django_db
def test_sharing_grants_reading_never_writing(client, library):
    """Someone else's shared list is a thing to read, not to edit."""
    collection = collections.create(library['owner'], 'Shared', shared=True)
    client.force_login(library['other'])

    for url in (reverse('web:collection_add', args=[collection.id, library['first'].id]),
                reverse('web:collection_delete', args=[collection.id]),
                reverse('web:collection_share', args=[collection.id])):
        assert client.post(url).status_code == 404, url

    assert Collection.objects.filter(pk=collection.pk).exists()


@pytest.mark.django_db
def test_a_list_can_be_shared_and_taken_back(client, library):
    collection = collections.create(library['owner'], 'Mine')

    client.post(reverse('web:collection_share', args=[collection.id]), {'shared': '1'})
    assert Collection.objects.get().shared is True

    client.post(reverse('web:collection_share', args=[collection.id]), {'shared': '0'})
    assert Collection.objects.get().shared is False


@pytest.mark.django_db
def test_an_anonymous_visitor_cannot_make_lists(client, library):
    client.logout()
    assert create(client, 'sneaky').status_code in (302, 403)
    assert not Collection.objects.exists()


@pytest.mark.django_db
def test_get_cannot_change_anything(client, library):
    collection = collections.create(library['owner'], 'Mine')
    for url in (reverse('web:collection_create'),
                reverse('web:collection_delete', args=[collection.id]),
                reverse('web:collection_add', args=[collection.id, library['first'].id])):
        assert client.get(url).status_code == 405, url


# --- browsing --------------------------------------------------------------

@pytest.mark.django_db
def test_the_page_lists_yours_and_shared_ones(client, library):
    collections.create(library['owner'], 'Mine')
    theirs = collections.create(library['other'], 'Theirs')
    theirs.shared = True
    theirs.save()
    collections.create(library['other'], 'Their secret')

    body = client.get(reverse('web:collections')).content.decode()
    assert 'Mine' in body and 'Theirs' in body
    assert 'Their secret' not in body


@pytest.mark.django_db
def test_your_own_lists_come_first(client, library):
    theirs = collections.create(library['other'], 'AAA theirs')
    theirs.shared = True
    theirs.save()
    collections.create(library['owner'], 'ZZZ mine')

    names = [c.name for c in collections.visible(library['owner'])]
    assert names == ['ZZZ mine', 'AAA theirs']


@pytest.mark.django_db
def test_the_card_offers_your_lists(client, library):
    collections.create(library['owner'], 'On the card')
    body = client.get(reverse('web:searchbooks'),
                      {'searchtype': 'm', 'searchterms': 'FIRST'}).content.decode()
    assert 'On the card' in body
    assert 'collectionToggle' in body


@pytest.mark.django_db
def test_the_nav_links_to_the_lists(client, library):
    assert reverse('web:collections') in client.get(reverse('web:main')).content.decode()


# --- OPDS ------------------------------------------------------------------

@pytest.mark.django_db
def test_the_opds_feed_shows_yours_and_shared(client, library):
    collections.create(library['owner'], 'Mine')
    theirs = collections.create(library['other'], 'Theirs')
    theirs.shared = True
    theirs.save()
    collections.create(library['other'], 'Their secret')

    body = client.get(reverse('opds:collections')).content.decode()
    assert 'Mine' in body
    assert 'Theirs (other)' in body       # whose it is, for a reader on an e-reader
    assert 'Their secret' not in body


@pytest.mark.django_db
def test_an_opds_entry_leads_to_the_books(client, library):
    collection = collections.create(library['owner'], 'Followed')
    collections.add_book(collection, library['first'])

    body = client.get(reverse('opds:searchbooks',
                              kwargs={'searchtype': 'c',
                                      'searchterms': collection.id})).content.decode()
    assert 'First book' in body
    assert 'Second book' not in body


@pytest.mark.django_db
def test_opds_refuses_someone_elses_private_list(client, library):
    collection = collections.create(library['owner'], 'Private')
    client.force_login(library['other'])

    resp = client.get(reverse('opds:searchbooks',
                              kwargs={'searchtype': 'c', 'searchterms': collection.id}))
    assert resp.status_code == 404


# --- helpers ---------------------------------------------------------------

@pytest.mark.django_db
def test_containing_is_one_query(library, django_assert_num_queries):
    collection = collections.create(library['owner'], 'Mine')
    collections.add_book(collection, library['first'])

    with django_assert_num_queries(1):
        got = collections.containing(library['owner'],
                                     [library['first'].id, library['second'].id])
    assert got == {library['first'].id: [collection.id]}


@pytest.mark.django_db
def test_containing_shows_only_your_own_membership(library):
    """The picker is for your lists; someone else's must not tick a box."""
    theirs = collections.create(library['other'], 'Theirs')
    theirs.shared = True
    theirs.save()
    collections.add_book(theirs, library['first'])

    assert collections.containing(library['owner'], [library['first'].id]) == {}
