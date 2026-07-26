"""Self-service accounts: change a password, reset a forgotten one, register.

All three used to require an administrator with admin access.
"""
import pytest
from django.core import mail as django_mail
from django.core.cache import cache
from django.urls import reverse
from constance import config


@pytest.fixture(autouse=True)
def clean(db):
    cache.clear()
    config.SOPDS_AUTH = True
    config.SOPDS_ALLOW_REGISTRATION = False
    config.SOPDS_SMTP_HOST = 'smtp.example.com'
    config.SOPDS_MAIL_FROM = 'library@example.com'
    yield
    cache.clear()


@pytest.fixture
def reader(django_user_model):
    return django_user_model.objects.create_user(
        username='reader', password='old-password-42', email='reader@example.com')


# --- changing a known password ---------------------------------------------

@pytest.mark.django_db
def test_a_signed_in_reader_can_change_their_password(client, reader):
    client.force_login(reader)
    resp = client.post(reverse('web:password_change'), {
        'old_password': 'old-password-42',
        'new_password1': 'a-new-password-99',
        'new_password2': 'a-new-password-99'})
    assert resp.status_code == 302

    reader.refresh_from_db()
    assert reader.check_password('a-new-password-99')


@pytest.mark.django_db
def test_the_old_password_is_required(client, reader):
    client.force_login(reader)
    client.post(reverse('web:password_change'), {
        'old_password': 'not-it',
        'new_password1': 'a-new-password-99',
        'new_password2': 'a-new-password-99'})

    reader.refresh_from_db()
    assert reader.check_password('old-password-42')


@pytest.mark.django_db
def test_changing_a_password_needs_a_login(client, reader):
    resp = client.get(reverse('web:password_change'))
    assert resp.status_code in (302, 403)


@pytest.mark.django_db
def test_the_settings_page_links_to_it(client, reader):
    client.force_login(reader)
    body = client.get(reverse('web:settings')).content.decode()
    assert reverse('web:password_change') in body


# --- resetting a forgotten one ---------------------------------------------

@pytest.mark.django_db
def test_a_reset_sends_a_link(client, reader):
    resp = client.post(reverse('web:password_reset'), {'email': 'reader@example.com'})
    assert resp.status_code == 302
    assert len(django_mail.outbox) == 1
    assert reverse('web:password_reset_confirm',
                   kwargs={'uidb64': 'x', 'token': 'y'})[:20] in django_mail.outbox[0].body


@pytest.mark.django_db
def test_the_link_actually_sets_a_new_password(client, reader):
    client.post(reverse('web:password_reset'), {'email': 'reader@example.com'})
    body = django_mail.outbox[0].body
    link = next(line.strip() for line in body.splitlines() if '/password/reset/' in line)
    path = link.split('://', 1)[1].split('/', 1)[1]

    # Django swaps the token for a session-held one and redirects.
    resp = client.get('/' + path, follow=True)
    assert resp.status_code == 200
    resp = client.post(resp.request['PATH_INFO'],
                       {'new_password1': 'reset-password-77',
                        'new_password2': 'reset-password-77'})
    assert resp.status_code == 302

    reader.refresh_from_db()
    assert reader.check_password('reset-password-77')


@pytest.mark.django_db
def test_an_unknown_address_looks_exactly_the_same(client, reader):
    """The form must not double as a way to find out who has an account."""
    known = client.post(reverse('web:password_reset'), {'email': 'reader@example.com'})
    django_mail.outbox.clear()
    unknown = client.post(reverse('web:password_reset'), {'email': 'nobody@example.com'})

    assert known.status_code == unknown.status_code == 302
    assert known.url == unknown.url
    assert django_mail.outbox == []


@pytest.mark.django_db
def test_reset_is_hidden_when_mail_is_not_configured(client, reader):
    """An offer that cannot work is worse than no offer."""
    config.SOPDS_SMTP_HOST = ''
    assert client.get(reverse('web:password_reset')).status_code == 404
    assert reverse('web:password_reset') not in client.get(reverse('web:login')).content.decode()


@pytest.mark.django_db
def test_reset_is_offered_on_the_login_page_when_it_can_work(client):
    assert reverse('web:password_reset') in client.get(reverse('web:login')).content.decode()


@pytest.mark.django_db
def test_reset_requests_are_throttled(client, reader):
    """It sends mail to an address the caller picks, so it is usable both to
    spam a stranger and to probe for accounts."""
    for _ in range(10):
        client.post(reverse('web:password_reset'), {'email': 'reader@example.com'})

    resp = client.post(reverse('web:password_reset'), {'email': 'reader@example.com'})
    assert resp.status_code == 403


# --- registration ----------------------------------------------------------

@pytest.mark.django_db
def test_registration_is_off_by_default(client):
    assert client.get(reverse('web:register')).status_code == 404


@pytest.mark.django_db
def test_registration_is_not_advertised_while_it_is_off(client):
    assert reverse('web:register') not in client.get(reverse('web:login')).content.decode()


@pytest.mark.django_db
def test_a_visitor_can_register_when_it_is_allowed(client, django_user_model):
    config.SOPDS_ALLOW_REGISTRATION = True
    resp = client.post(reverse('web:register'), {
        'username': 'newcomer',
        'password1': 'a-fresh-password-1',
        'password2': 'a-fresh-password-1'})

    assert resp.status_code == 302
    assert django_user_model.objects.filter(username='newcomer').exists()


@pytest.mark.django_db
def test_registering_signs_you_in(client, django_user_model):
    config.SOPDS_ALLOW_REGISTRATION = True
    client.post(reverse('web:register'), {
        'username': 'newcomer', 'password1': 'a-fresh-password-1',
        'password2': 'a-fresh-password-1'})
    assert client.session.get('_auth_user_id')


@pytest.mark.django_db
def test_a_weak_password_is_refused(client, django_user_model):
    config.SOPDS_ALLOW_REGISTRATION = True
    resp = client.post(reverse('web:register'), {
        'username': 'newcomer', 'password1': '123', 'password2': '123'})

    assert resp.status_code == 200        # redisplayed with errors
    assert not django_user_model.objects.filter(username='newcomer').exists()


@pytest.mark.django_db
def test_a_duplicate_username_is_refused(client, reader):
    config.SOPDS_ALLOW_REGISTRATION = True
    resp = client.post(reverse('web:register'), {
        'username': 'reader', 'password1': 'a-fresh-password-1',
        'password2': 'a-fresh-password-1'})
    assert resp.status_code == 200
    assert reader.check_password('old-password-42')     # untouched


@pytest.mark.django_db
def test_registration_is_throttled(client, django_user_model):
    config.SOPDS_ALLOW_REGISTRATION = True
    for n in range(10):
        client.logout()
        client.post(reverse('web:register'), {
            'username': 'u%d' % n, 'password1': 'a-fresh-password-1',
            'password2': 'a-fresh-password-1'})

    client.logout()
    resp = client.post(reverse('web:register'), {
        'username': 'toomany', 'password1': 'a-fresh-password-1',
        'password2': 'a-fresh-password-1'})
    assert resp.status_code == 403
    assert not django_user_model.objects.filter(username='toomany').exists()


# --- mail configuration ----------------------------------------------------

def test_mail_is_only_configured_when_it_could_work():
    from sopds import email as mail

    config.SOPDS_SMTP_HOST = 'smtp.example.com'
    config.SOPDS_MAIL_FROM = 'library@example.com'
    assert mail.is_configured()

    config.SOPDS_SMTP_HOST = ''
    assert not mail.is_configured()

    config.SOPDS_SMTP_HOST = 'smtp.example.com'
    config.SOPDS_MAIL_FROM = '   '
    assert not mail.is_configured()


def test_the_backend_takes_its_settings_from_the_admin():
    from sopds.email import ConstanceEmailBackend

    config.SOPDS_SMTP_HOST = 'mail.example.net'
    config.SOPDS_SMTP_PORT = 2525
    config.SOPDS_SMTP_USER = 'postman'
    config.SOPDS_SMTP_TLS = False

    backend = ConstanceEmailBackend()
    assert (backend.host, backend.port, backend.username) == ('mail.example.net', 2525, 'postman')
    assert backend.use_tls is False


def test_an_explicit_argument_still_wins():
    from sopds.email import ConstanceEmailBackend

    config.SOPDS_SMTP_HOST = 'mail.example.net'
    assert ConstanceEmailBackend(host='override.example.org').host == 'override.example.org'
