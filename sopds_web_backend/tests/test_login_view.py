"""Tests for LoginView, focused on the open-redirect protection of ?next=."""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse


@pytest.fixture
def user(db):
    return User.objects.create_user(username="alice", password="s3cret")


def _login(client, next_value, username="alice", password="s3cret"):
    url = reverse("web:login")
    if next_value is not None:
        url = f"{url}?next={next_value}"
    return client.post(url, {"username": username, "password": password})


@pytest.mark.django_db
@pytest.mark.parametrize(
    "evil_next",
    [
        "http://evil.com",
        "https://evil.com/phish",
        "//evil.com",
        "https:///evil.com",
        "\\\\evil.com",
    ],
)
def test_login_rejects_offsite_next(client, user, evil_next):
    """An off-site ?next= must not be honoured; fall back to the main page."""
    resp = _login(client, evil_next)
    assert resp.status_code == 302
    assert resp.url == reverse("web:main")


@pytest.mark.django_db
def test_login_allows_safe_relative_next(client, user):
    """A same-site relative ?next= is preserved after login."""
    safe = reverse("web:book")
    resp = _login(client, safe)
    assert resp.status_code == 302
    assert resp.url == safe


@pytest.mark.django_db
def test_login_without_next_goes_to_main(client, user):
    resp = _login(client, None)
    assert resp.status_code == 302
    assert resp.url == reverse("web:main")


@pytest.mark.django_db
def test_login_get_renders_form(client):
    """GET (no credentials) renders the login form, not a redirect."""
    resp = client.get(reverse("web:login"))
    assert resp.status_code == 200
