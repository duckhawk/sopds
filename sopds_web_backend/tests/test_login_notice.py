"""The notice above the login form.

Somewhere to say who to ask for an account, or — on a public demo — what to log
in with. Without it the only way to tell a visitor anything is to edit a
template, which a deployment cannot do.
"""
import pytest
from django.urls import reverse
from constance import config


@pytest.fixture
def site(db):
    config.SOPDS_AUTH = True
    config.SOPDS_LOGIN_NOTICE = ''
    return None


@pytest.mark.django_db
def test_the_notice_is_shown(client, site):
    config.SOPDS_LOGIN_NOTICE = 'Log in as demo / demo to look around.'
    body = client.get(reverse('web:login')).content.decode()
    assert 'Log in as demo / demo to look around.' in body


@pytest.mark.django_db
def test_nothing_is_shown_when_it_is_empty(client, site):
    body = client.get(reverse('web:login')).content.decode()
    assert '<div class="callout">' not in body


@pytest.mark.django_db
def test_it_is_escaped(client, site):
    """It is administrator-supplied text, not markup."""
    config.SOPDS_LOGIN_NOTICE = '<script>alert(1)</script>'
    body = client.get(reverse('web:login')).content.decode()
    assert '<script>alert(1)</script>' not in body
    assert '&lt;script&gt;' in body
