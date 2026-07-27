"""The documentation at /docs.

Two things are worth guarding here: that the pages the repository ships are
actually reachable and internally consistent, and that the section stays
readable by someone who is not signed in — which is exactly who needs the page
about connecting a reader.
"""
import os
import re

import pytest
from django.test import Client
from django.urls import reverse
from constance import config

from sopds_docs import pages

LANGUAGES = ('en', 'ru')


def slugs(lang):
    return [slug for slug, _title in pages.index(lang)]


# --- what the repository ships ---------------------------------------------

def test_there_is_documentation_at_all():
    assert slugs('en')


@pytest.mark.parametrize('lang', LANGUAGES)
def test_every_page_has_a_title(lang):
    """The heading is what the sidebar shows; a page without one shows a slug."""
    for slug, title in pages.index(lang):
        assert title and title != slug, slug


def test_the_translations_cover_the_same_pages():
    """A missing translation falls back to English rather than 404ing, but a
    page silently absent from one language is a mistake, not a decision."""
    assert slugs('ru') == slugs('en')


@pytest.mark.parametrize('lang', LANGUAGES)
def test_internal_links_point_at_pages_that_exist(lang):
    """A dead link inside the documentation is the easiest kind to miss."""
    known = set(slugs(lang))
    directory = os.path.join(pages.CONTENT, lang)

    for name in os.listdir(directory):
        with open(os.path.join(directory, name), encoding='utf-8') as f:
            text = f.read()
        for target in re.findall(r'\]\(/docs/([a-z0-9-]+)/\)', text):
            assert target in known, '%s/%s links to /docs/%s/' % (lang, name, target)


@pytest.mark.parametrize('lang', LANGUAGES)
def test_pages_render_to_html(lang):
    for slug in slugs(lang):
        html, found = pages.render(slug, lang)
        assert html and html.startswith('<h1'), slug
        assert found == lang


def test_an_unknown_page_is_not_rendered():
    assert pages.render('no-such-page') == (None, None)


def test_a_page_missing_from_a_translation_falls_back_to_english(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, 'CONTENT', str(tmp_path))
    (tmp_path / 'en').mkdir()
    (tmp_path / 'ru').mkdir()
    (tmp_path / 'en' / '10-only-english.md').write_text('# Only English\n\nText.\n')

    html, lang = pages.render('only-english', 'ru')
    assert lang == 'en'
    assert 'Only English' in html


# --- serving ----------------------------------------------------------------

@pytest.mark.django_db
def test_the_index_leads_to_the_first_page(client):
    resp = client.get(reverse('docs:index'))
    assert resp.status_code == 302
    assert resp['Location'] == reverse('docs:page', args=[slugs('en')[0]])


@pytest.mark.django_db
def test_a_page_is_served(client):
    resp = client.get(reverse('docs:page', args=['getting-started']))
    assert resp.status_code == 200
    assert b'<h1' in resp.content


@pytest.mark.django_db
def test_an_unknown_page_is_a_404(client):
    assert client.get(reverse('docs:page', args=['nonsense'])).status_code == 404


@pytest.mark.django_db
def test_the_sidebar_lists_every_page(client):
    body = client.get(reverse('docs:page', args=['getting-started'])).content.decode()
    for slug in slugs('en'):
        assert reverse('docs:page', args=[slug]) in body, slug


@pytest.mark.django_db
def test_documentation_is_readable_without_signing_in(db):
    """The page about connecting a reader is what someone who cannot get in
    yet needs; putting it behind the login wall would be perverse."""
    config.SOPDS_AUTH = True
    resp = Client().get(reverse('docs:page', args=['e-readers-and-opds']))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_the_pages_follow_the_interface_language(client):
    """Which is SOPDS_LANGUAGE, set by the administrator — this installation
    does not switch language per visitor."""
    config.SOPDS_LANGUAGE = 'ru-RU'
    body = client.get(reverse('docs:page', args=['getting-started'])).content.decode()
    assert 'С чего начать' in body

    config.SOPDS_LANGUAGE = 'en-US'
    body = client.get(reverse('docs:page', args=['getting-started'])).content.decode()
    assert 'Getting started' in body


@pytest.mark.django_db
def test_only_GET_is_allowed(client):
    assert client.post(reverse('docs:page', args=['getting-started'])).status_code == 405


@pytest.mark.django_db
def test_the_nav_and_the_welcome_page_link_to_the_documentation(client):
    config.SOPDS_AUTH = False
    body = client.get(reverse('web:main')).content.decode()
    assert reverse('docs:index') in body
