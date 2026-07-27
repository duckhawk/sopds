"""Letting a visitor choose the interface language.

The catalogue's language has always been one setting for the whole site, which
is right for a private library and wrong for a public one, where readers do not
share a first language. Turning the switcher on lets each session pick; leaving
it off keeps the old behaviour exactly.
"""
import pytest
from django.urls import reverse
from constance import config

from opds_catalog.middleware import LANGUAGE_SESSION_KEY


@pytest.fixture
def site(db):
    config.SOPDS_AUTH = False
    config.SOPDS_LANGUAGE = 'en-US'
    config.SOPDS_LANGUAGE_SWITCHER = True
    config.SOPDS_LOGIN_NOTICE_EN = ''
    config.SOPDS_LOGIN_NOTICE_RU = ''
    return None


def switch(client, code, next_url='/web/'):
    return client.post(reverse('web:setlanguage'), {'language': code, 'next': next_url})


# --- switching --------------------------------------------------------------

@pytest.mark.django_db
def test_a_visitor_can_switch_to_russian(client, site):
    assert 'Recently added' in client.get(reverse('web:main')).content.decode()

    switch(client, 'ru-RU')
    assert 'Недавно добавленные' in client.get(reverse('web:main')).content.decode()


@pytest.mark.django_db
def test_and_back_again(client, site):
    switch(client, 'ru-RU')
    switch(client, 'en-US')
    assert 'Recently added' in client.get(reverse('web:main')).content.decode()


@pytest.mark.django_db
def test_the_choice_survives_the_next_page(client, site):
    switch(client, 'ru-RU')
    for url in (reverse('web:main'), reverse('web:catalog'), reverse('docs:index')):
        resp = client.get(url, follow=True)
        assert resp.status_code == 200
    assert client.session[LANGUAGE_SESSION_KEY] == 'ru-RU'


@pytest.mark.django_db
def test_the_documentation_follows_the_switch(client, site):
    switch(client, 'ru-RU')
    body = client.get(reverse('docs:page', args=['getting-started'])).content.decode()
    assert 'С чего начать' in body


@pytest.mark.django_db
def test_the_page_declares_the_language_it_is_in(client, site):
    """For a screen reader and for the browser's own translation offer."""
    switch(client, 'ru-RU')
    assert '<html lang="ru-RU"' in client.get(reverse('web:main')).content.decode()


# --- what it refuses --------------------------------------------------------

@pytest.mark.django_db
def test_a_language_that_is_not_offered_is_ignored(client, site):
    switch(client, 'de-DE')
    assert LANGUAGE_SESSION_KEY not in client.session
    assert 'Recently added' in client.get(reverse('web:main')).content.decode()


@pytest.mark.django_db
def test_a_stale_choice_falls_back_to_the_site_default(client, site):
    """A language could be dropped from the offered set while sessions live on."""
    session = client.session
    session[LANGUAGE_SESSION_KEY] = 'eo'
    session.save()

    assert 'Recently added' in client.get(reverse('web:main')).content.decode()


@pytest.mark.django_db
def test_it_cannot_be_used_to_bounce_someone_off_the_site(client, site):
    resp = client.post(reverse('web:setlanguage'),
                       {'language': 'ru-RU', 'next': 'https://elsewhere.example/'})
    assert resp['Location'] == reverse('web:main')


@pytest.mark.django_db
def test_get_does_not_change_the_language(client, site):
    """A prefetching browser must not flip a reader's language for them."""
    assert client.get(reverse('web:setlanguage')).status_code == 405


# --- off by default ---------------------------------------------------------

@pytest.mark.django_db
def test_with_the_switcher_off_the_control_is_absent(client, site):
    config.SOPDS_LANGUAGE_SWITCHER = False
    assert reverse('web:setlanguage') not in client.get(reverse('web:main')).content.decode()


@pytest.mark.django_db
def test_with_the_switcher_off_a_session_choice_is_not_honoured(client, site):
    """Otherwise turning it off would leave people stuck in whatever they picked."""
    switch(client, 'ru-RU')
    config.SOPDS_LANGUAGE_SWITCHER = False

    assert 'Recently added' in client.get(reverse('web:main')).content.decode()


@pytest.mark.django_db
def test_the_control_is_shown_when_it_is_on(client, site):
    body = client.get(reverse('web:main')).content.decode()
    assert reverse('web:setlanguage') in body
    assert 'RU' in body and 'EN' in body


# --- the cached page fragments ---------------------------------------------

@pytest.mark.django_db
def test_a_cached_page_is_not_served_in_the_wrong_language(client, site):
    """The book listing caches its body fragment. Before the language was part
    of that key, switching served whatever had been rendered first."""
    config.SOPDS_CACHE_TIME = 300
    url = reverse('web:searchbooks')

    client.get(url, {'searchtype': 'n'})
    switch(client, 'ru-RU')
    body = client.get(url, {'searchtype': 'n'}).content.decode()

    assert 'Book title:' not in body


# --- the notice above the login form ---------------------------------------

@pytest.mark.django_db
def test_the_notice_is_shown_in_the_language_being_read(client, site):
    config.SOPDS_AUTH = True
    config.SOPDS_LOGIN_NOTICE_EN = 'Log in as demo / demo.'
    config.SOPDS_LOGIN_NOTICE_RU = 'Войдите как demo / demo.'

    assert 'Log in as demo / demo.' in client.get(reverse('web:login')).content.decode()
    switch(client, 'ru-RU', next_url='/web/login/')
    assert 'Войдите как demo / demo.' in client.get(reverse('web:login')).content.decode()


@pytest.mark.django_db
def test_one_notice_is_enough(client, site):
    """An installation that filled in only one still wants it read."""
    config.SOPDS_AUTH = True
    config.SOPDS_LOGIN_NOTICE_EN = 'Ask the librarian for an account.'

    switch(client, 'ru-RU', next_url='/web/login/')
    assert 'Ask the librarian' in client.get(reverse('web:login')).content.decode()


@pytest.mark.django_db
def test_no_notice_means_no_callout(client, site):
    config.SOPDS_AUTH = True
    assert '<div class="callout">' not in client.get(reverse('web:login')).content.decode()


@pytest.mark.django_db
def test_the_notice_is_escaped(client, site):
    """It is administrator-supplied text, not markup."""
    config.SOPDS_AUTH = True
    config.SOPDS_LOGIN_NOTICE_EN = '<script>alert(1)</script>'

    body = client.get(reverse('web:login')).content.decode()
    assert '<script>alert(1)</script>' not in body
    assert '&lt;script&gt;' in body
