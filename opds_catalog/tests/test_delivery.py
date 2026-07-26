"""Mailing a book to a reading device.

An e-reader without an OPDS client can only be filled by sending files to its
address; until now the only way out of this catalogue was a browser download
and a cable.
"""
import os

import pytest
from django.core import mail as django_mail
from django.core.cache import cache
from django.urls import reverse
from constance import config

from opds_catalog import delivery
from opds_catalog.models import Book, BookStat, Catalog, Theme

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FB2 = '262001.fb2'


@pytest.fixture(autouse=True)
def clean(db):
    cache.clear()
    config.SOPDS_AUTH = True
    config.SOPDS_RATE_LIMIT = 0
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_SMTP_HOST = 'smtp.example.com'
    config.SOPDS_MAIL_FROM = 'library@example.com'
    yield
    cache.clear()


@pytest.fixture
def setup(db, django_user_model, client):
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    book = Book.objects.create(
        filename=FB2, path='.', filesize=os.path.getsize(os.path.join(DATA, FB2)),
        format='fb2', cat_type=0, docdate='2011', lang='en', title='The Sparrow',
        search_title='THE SPARROW', annotation='', avail=2, catalog=cat)
    user = django_user_model.objects.create_user(username='reader', password='pw')
    Theme.objects.create(user=user, device_email='reader@kindle.com')
    client.force_login(user)
    return {'book': book, 'user': user}


def send(client, book):
    return client.post(reverse('web:send_to_device', args=[book.id]))


# --- sending ---------------------------------------------------------------

@pytest.mark.django_db
def test_a_book_is_mailed_to_the_configured_address(client, setup):
    resp = send(client, setup['book'])
    assert resp.status_code == 200 and resp.json()['ok']

    assert len(django_mail.outbox) == 1
    message = django_mail.outbox[0]
    assert message.to == ['reader@kindle.com']
    assert message.from_email == 'library@example.com'


@pytest.mark.django_db
def test_the_book_travels_as_an_attachment(client, setup):
    send(client, setup['book'])
    name, content, mimetype = django_mail.outbox[0].attachments[0]

    assert name.endswith('.fb2')
    assert len(content) > 1000
    assert 'fb2' in mimetype or 'xml' in mimetype


@pytest.mark.django_db
def test_the_subject_is_the_title(client, setup):
    """Amazon uses it as the document title and ignores the body."""
    send(client, setup['book'])
    assert django_mail.outbox[0].subject == 'The Sparrow'


@pytest.mark.django_db
def test_sending_counts_as_taking_the_book_out(client, setup):
    send(client, setup['book'])
    assert BookStat.objects.get(book=setup['book']).downloads == 1


# --- refusals --------------------------------------------------------------

@pytest.mark.django_db
def test_it_refuses_when_no_address_is_set(client, setup):
    Theme.objects.update(device_email='')
    resp = send(client, setup['book'])

    assert resp.status_code == 400
    assert not resp.json()['ok'] and resp.json()['error']
    assert django_mail.outbox == []


@pytest.mark.django_db
def test_it_refuses_when_mail_is_not_configured(client, setup):
    config.SOPDS_SMTP_HOST = ''
    resp = send(client, setup['book'])
    assert resp.status_code == 400
    assert django_mail.outbox == []


@pytest.mark.django_db
def test_it_refuses_a_book_whose_file_is_gone(client, setup, tmp_path):
    config.SOPDS_ROOT_LIB = str(tmp_path)
    resp = send(client, setup['book'])
    assert resp.status_code == 400
    assert django_mail.outbox == []


@pytest.mark.django_db
def test_an_oversized_book_is_refused_before_the_relay_rejects_it(client, setup, monkeypatch):
    """A clear reason beats a message that vanishes somewhere in a relay."""
    monkeypatch.setattr(delivery, 'MAX_ATTACHMENT_BYTES', 100)
    resp = send(client, setup['book'])

    assert resp.status_code == 400
    assert 'MB' in resp.json()['error']
    assert django_mail.outbox == []


@pytest.mark.django_db
def test_an_smtp_failure_does_not_leak_the_relay_into_the_page(client, setup, monkeypatch):
    """The reason belongs in the log: it can name the host and the credentials
    it rejected."""
    def boom(self, *args, **kwargs):
        raise RuntimeError('535 auth failed for postman@relay.internal')

    monkeypatch.setattr('django.core.mail.EmailMessage.send', boom)
    resp = send(client, setup['book'])

    assert resp.status_code == 400
    assert 'relay.internal' not in resp.json()['error']
    assert '535' not in resp.json()['error']


# --- access ----------------------------------------------------------------

@pytest.mark.django_db
def test_get_is_not_allowed(client, setup):
    """It sends a message, so it must not be reachable by a link."""
    resp = client.get(reverse('web:send_to_device', args=[setup['book'].id]))
    assert resp.status_code == 405
    assert django_mail.outbox == []


@pytest.mark.django_db
def test_an_anonymous_caller_cannot_send(client, setup):
    client.logout()
    resp = send(client, setup['book'])
    assert resp.status_code in (302, 403)
    assert django_mail.outbox == []


@pytest.mark.django_db
def test_sending_counts_against_the_rate_limit(client, setup):
    """Sending mail on demand is the most abusable thing here."""
    config.SOPDS_RATE_LIMIT = 2
    for _ in range(2):
        send(client, setup['book'])

    resp = send(client, setup['book'])
    assert resp.status_code == 429
    assert len(django_mail.outbox) == 2


@pytest.mark.django_db
def test_a_missing_book_is_404(client, setup):
    assert client.post(reverse('web:send_to_device', args=[999999])).status_code == 404


# --- what the UI offers ----------------------------------------------------

@pytest.mark.django_db
def test_the_button_appears_only_when_sending_could_work(client, setup):
    # The button, not the helper function: that is defined in the page script
    # either way, and only the button is conditional.
    button = 'onclick="sendToDevice(%d)' % setup['book'].id

    body = client.get(reverse('web:searchbooks'),
                      {'searchtype': 'm', 'searchterms': 'SPARROW'}).content.decode()
    assert button in body

    Theme.objects.update(device_email='')
    body = client.get(reverse('web:searchbooks'),
                      {'searchtype': 'm', 'searchterms': 'SPARROW'}).content.decode()
    assert button not in body


@pytest.mark.django_db
def test_the_address_is_set_from_the_settings_page(client, setup):
    client.post(reverse('web:settings'), {'theme': 'light', 'reader_mode': 'whole',
                                          'font_size': '100',
                                          'device_email': 'other@kindle.com'})
    assert Theme.objects.get().device_email == 'other@kindle.com'


@pytest.mark.django_db
def test_the_address_field_is_hidden_without_mail(client, setup):
    config.SOPDS_SMTP_HOST = ''
    body = client.get(reverse('web:settings')).content.decode()
    assert 'device_email' not in body


# --- helpers ---------------------------------------------------------------

@pytest.mark.django_db
def test_can_send_needs_both_a_server_and_an_address(setup):
    assert delivery.can_send(setup['user'])

    Theme.objects.update(device_email='')
    assert not delivery.can_send(setup['user'])

    Theme.objects.update(device_email='reader@kindle.com')
    config.SOPDS_SMTP_HOST = ''
    assert not delivery.can_send(setup['user'])


@pytest.mark.django_db
def test_formats_a_kindle_will_not_take_are_flagged(setup):
    book = setup['book']
    assert delivery.warning_for(book)          # fb2

    book.format = 'epub'
    assert delivery.warning_for(book) is None
