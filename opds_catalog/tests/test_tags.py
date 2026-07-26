"""Free-form labels on books.

Genres arrive with the files and follow a taxonomy nobody here chose; tags are
whatever this library's readers find worth marking.
"""
import pytest
from django.urls import reverse
from constance import config

from opds_catalog import tags
from opds_catalog.models import Book, Catalog, Counter, Tag, btag


@pytest.fixture
def library(db, django_user_model, client):
    config.SOPDS_AUTH = True
    config.SOPDS_TAGS_EDITABLE = True
    config.SOPDS_DOUBLES_HIDE = False
    config.SOPDS_MAXITEMS = 60
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)

    def add(title):
        return Book.objects.create(
            filename='%s.fb2' % title, path='.', filesize=1, format='fb2', cat_type=0,
            docdate='2011', lang='en', title=title, search_title=title.upper(),
            annotation='', avail=2, catalog=cat)

    books = {'first': add('First book'), 'second': add('Second book')}
    client.force_login(django_user_model.objects.create_user(username='r', password='pw'))
    Counter.objects.update_known_counters()
    return books


def post_add(client, book, name):
    return client.post(reverse('web:tag_add', args=[book.id]), {'name': name})


# --- naming ----------------------------------------------------------------

@pytest.mark.django_db
def test_a_tag_can_be_put_on_a_book(client, library):
    resp = post_add(client, library['first'], 'book club')
    assert resp.status_code == 200 and resp.json()['ok']
    assert [t.name for t in library['first'].tags.all()] == ['book club']


@pytest.mark.django_db
def test_case_does_not_create_a_second_tag(client, library):
    """The commonest way a shared vocabulary quietly falls apart."""
    post_add(client, library['first'], 'Book Club')
    post_add(client, library['second'], 'book club')

    assert Tag.objects.count() == 1
    assert Tag.objects.get().name == 'Book Club'      # the first spelling wins


@pytest.mark.django_db
def test_surrounding_and_repeated_whitespace_is_collapsed(client, library):
    post_add(client, library['first'], '  book   club  ')
    assert Tag.objects.get().name == 'book club'


@pytest.mark.django_db
@pytest.mark.parametrize('name', ['', '   ', '\t\n'])
def test_an_empty_name_is_refused(client, library, name):
    assert post_add(client, library['first'], name).status_code == 400
    assert not Tag.objects.exists()


@pytest.mark.django_db
def test_an_overlong_name_is_truncated_not_rejected(client, library):
    post_add(client, library['first'], 'x' * 500)
    assert len(Tag.objects.get().name) == 64


@pytest.mark.django_db
def test_tagging_twice_is_not_an_error_and_not_a_duplicate(client, library):
    post_add(client, library['first'], 'twice')
    post_add(client, library['first'], 'twice')
    assert btag.objects.filter(book=library['first']).count() == 1


@pytest.mark.django_db
def test_a_book_cannot_be_buried_in_tags(client, library):
    for n in range(tags.MAX_PER_BOOK):
        post_add(client, library['first'], 'tag %d' % n)

    assert post_add(client, library['first'], 'one too many').status_code == 400
    assert library['first'].tags.count() == tags.MAX_PER_BOOK


# --- removing --------------------------------------------------------------

@pytest.mark.django_db
def test_a_tag_can_be_taken_off(client, library):
    post_add(client, library['first'], 'wrong')
    tag = Tag.objects.get()

    resp = client.post(reverse('web:tag_remove', args=[library['first'].id]), {'tag': tag.id})
    assert resp.status_code == 200 and resp.json()['ok']
    assert not library['first'].tags.exists()


@pytest.mark.django_db
def test_a_tag_nothing_carries_disappears(client, library):
    """Otherwise the browse list fills with labels that match nothing, and
    there is nowhere to delete them from."""
    post_add(client, library['first'], 'orphan')
    tag = Tag.objects.get()
    client.post(reverse('web:tag_remove', args=[library['first'].id]), {'tag': tag.id})
    assert not Tag.objects.exists()


@pytest.mark.django_db
def test_a_tag_still_on_another_book_survives(client, library):
    post_add(client, library['first'], 'shared')
    post_add(client, library['second'], 'shared')
    tag = Tag.objects.get()

    client.post(reverse('web:tag_remove', args=[library['first'].id]), {'tag': tag.id})
    assert Tag.objects.filter(pk=tag.pk).exists()
    assert list(library['second'].tags.all()) == [tag]


# --- access ----------------------------------------------------------------

@pytest.mark.django_db
def test_get_cannot_change_tags(client, library):
    """They are shared metadata, so a link must not be able to alter them."""
    assert client.get(reverse('web:tag_add', args=[library['first'].id])).status_code == 405


@pytest.mark.django_db
def test_an_anonymous_visitor_cannot_tag(client, library):
    client.logout()
    resp = post_add(client, library['first'], 'sneaky')
    assert resp.status_code in (302, 403)
    assert not Tag.objects.exists()


@pytest.mark.django_db
def test_editing_can_be_closed_without_hiding_the_tags(client, library):
    """For a catalogue whose registration is open to strangers."""
    post_add(client, library['first'], 'existing')
    config.SOPDS_TAGS_EDITABLE = False

    assert post_add(client, library['first'], 'new one').status_code == 403
    body = client.get(reverse('web:tags')).content.decode()
    assert 'existing' in body


# --- browsing --------------------------------------------------------------

@pytest.mark.django_db
def test_the_tag_list_counts_the_books(client, library):
    post_add(client, library['first'], 'shared')
    post_add(client, library['second'], 'shared')

    body = client.get(reverse('web:tags')).content.decode()
    assert 'shared' in body and '2' in body


@pytest.mark.django_db
def test_a_tag_leads_to_its_books(client, library):
    post_add(client, library['first'], 'wanted')
    tag = Tag.objects.get()

    body = client.get(reverse('web:searchbooks'),
                      {'searchtype': 't', 'searchterms': tag.id}).content.decode()
    assert 'First book' in body
    assert '<b id="%d">Second book' % library['second'].id not in body


@pytest.mark.django_db
def test_an_unknown_tag_is_404_not_an_empty_page(client, library):
    resp = client.get(reverse('web:searchbooks'), {'searchtype': 't', 'searchterms': '9999'})
    assert resp.status_code == 404


@pytest.mark.django_db
def test_the_card_shows_the_tags(client, library):
    post_add(client, library['first'], 'on the card')
    body = client.get(reverse('web:searchbooks'),
                      {'searchtype': 'm', 'searchterms': 'FIRST'}).content.decode()
    assert 'on the card' in body


@pytest.mark.django_db
def test_the_nav_links_to_the_tag_list(client, library):
    assert reverse('web:tags') in client.get(reverse('web:main')).content.decode()


# --- OPDS ------------------------------------------------------------------

@pytest.mark.django_db
def test_the_opds_root_offers_tags(client, library):
    post_add(client, library['first'], 'anything')
    body = client.get(reverse('opds:main')).content.decode()
    assert 'By tags' in body
    assert reverse('opds:tags') in body


@pytest.mark.django_db
def test_the_opds_tag_feed_lists_them(client, library):
    post_add(client, library['first'], 'in the feed')
    body = client.get(reverse('opds:tags')).content.decode()
    assert 'in the feed' in body
    assert 'Books count: 1' in body


@pytest.mark.django_db
def test_an_opds_tag_entry_leads_to_its_books(client, library):
    post_add(client, library['first'], 'followed')
    tag = Tag.objects.get()

    body = client.get(reverse('opds:searchbooks',
                              kwargs={'searchtype': 't', 'searchterms': tag.id})).content.decode()
    assert 'First book' in body
    assert 'Second book' not in body


@pytest.mark.django_db
def test_unused_tags_are_not_listed(client, library):
    """`in_use` is what the browse lists show; a label on nothing is noise."""
    Tag.objects.create(name='never used', search_name='NEVER USED')
    assert list(tags.in_use()) == []


# --- the helper ------------------------------------------------------------

@pytest.mark.django_db
def test_for_books_is_one_query(library, django_assert_num_queries):
    tags.add(library['first'], 'a')
    tags.add(library['second'], 'b')

    ids = [library['first'].id, library['second'].id]
    with django_assert_num_queries(1):
        got = tags.for_books(ids)
    assert set(got) == set(ids)


@pytest.mark.django_db
def test_for_books_of_nothing_is_empty(library):
    assert tags.for_books([]) == {}
