"""Renaming the site without breaking what is already saved on people's readers.

An OPDS catalogue entry inside KOReader, a bookmark, a sync configuration — none
of those can be edited from here. So the old hostname keeps answering, and every
request to it is redirected to the new one, path and query intact.
"""
import pytest
from django.test import override_settings
from django.urls import reverse
from constance import config


NEW = 'lib.example.org'
OLD = 'sopds.example.org'


@pytest.fixture
def catalogue(db):
    config.SOPDS_AUTH = False
    return None


@override_settings(CANONICAL_HOST=NEW, ALLOWED_HOSTS=[NEW, OLD])
@pytest.mark.django_db
def test_the_old_host_redirects_permanently(client, catalogue):
    resp = client.get('/web/', HTTP_HOST=OLD)
    assert resp.status_code == 301
    assert resp['Location'] == 'http://%s/web/' % NEW


@override_settings(CANONICAL_HOST=NEW, ALLOWED_HOSTS=[NEW, OLD])
@pytest.mark.django_db
def test_the_path_and_query_survive(client, catalogue):
    """An e-reader asking for a search result must not land on the front page —
    which is exactly what the obvious ingress annotation would have done."""
    resp = client.get('/opds/search/books/m/tolstoy/', {'page': '2'}, HTTP_HOST=OLD)
    assert resp['Location'] == 'http://%s/opds/search/books/m/tolstoy/?page=2' % NEW


@override_settings(CANONICAL_HOST=NEW, ALLOWED_HOSTS=[NEW, OLD])
@pytest.mark.django_db
def test_the_new_host_is_served_directly(client, catalogue):
    assert client.get(reverse('web:main'), HTTP_HOST=NEW).status_code == 200


@override_settings(CANONICAL_HOST=NEW, ALLOWED_HOSTS=[NEW, OLD])
@pytest.mark.django_db
def test_a_port_on_the_host_is_not_a_different_host(client, catalogue):
    assert client.get(reverse('web:main'), HTTP_HOST='%s:8000' % NEW).status_code == 200


@override_settings(CANONICAL_HOST=NEW, ALLOWED_HOSTS=[NEW, OLD])
@pytest.mark.django_db
@pytest.mark.parametrize('path', ['/healthz', '/readyz'])
def test_probes_are_answered_not_redirected(client, catalogue, path):
    """A probe addresses the pod by whatever host it was given, and reads a
    redirect as a failure."""
    assert client.get(path, HTTP_HOST=OLD).status_code == 200


@override_settings(CANONICAL_HOST='', ALLOWED_HOSTS=[NEW, OLD])
@pytest.mark.django_db
def test_without_a_canonical_host_nothing_is_redirected(client, catalogue):
    """Which is every installation that has only ever had one name."""
    assert client.get(reverse('web:main'), HTTP_HOST=OLD).status_code == 200


@override_settings(CANONICAL_HOST=NEW, ALLOWED_HOSTS=[NEW, OLD])
@pytest.mark.django_db
def test_the_redirect_stays_on_https_behind_a_proxy(client, catalogue):
    """The scheme comes from X-Forwarded-Proto (SECURE_PROXY_SSL_HEADER). uwsgi
    is spoken to over plain HTTP by the ingress, so without that the redirect
    would quietly downgrade every saved catalogue to http://."""
    resp = client.get('/opds/', HTTP_HOST=OLD, HTTP_X_FORWARDED_PROTO='https')
    assert resp['Location'] == 'https://%s/opds/' % NEW
