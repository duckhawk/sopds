"""Web login form is brute-force throttled (#44)."""
import pytest
from django.urls import reverse
from django.core.cache import cache

from sopds_web_backend.views import LOGIN_RATE_LIMIT


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_login_locks_out_after_threshold(client, django_user_model):
    django_user_model.objects.create_user('good', 'g@x.y', 'pw')
    url = reverse('web:login')

    for _ in range(LOGIN_RATE_LIMIT):
        resp = client.post(url, {'username': 'good', 'password': 'wrong'})
        assert resp.status_code == 403

    # Now blocked: even the CORRECT password is refused (no redirect).
    resp = client.post(url, {'username': 'good', 'password': 'pw'})
    assert resp.status_code == 403


@pytest.mark.django_db
def test_successful_login_resets_the_counter(client, django_user_model):
    django_user_model.objects.create_user('good', 'g@x.y', 'pw')
    url = reverse('web:login')

    for _ in range(LOGIN_RATE_LIMIT - 1):
        client.post(url, {'username': 'good', 'password': 'wrong'})

    # Under the threshold the correct password still works and clears the count.
    resp = client.post(url, {'username': 'good', 'password': 'pw'})
    assert resp.status_code == 302

    # Counter cleared, so failures start from zero again (not immediately locked).
    resp = client.post(url, {'username': 'good', 'password': 'wrong'})
    assert resp.status_code == 403   # a normal failure, not the lockout
