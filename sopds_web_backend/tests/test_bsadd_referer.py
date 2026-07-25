"""BSAddView must not 500 when there is no Referer header (#52)."""
import pytest
from django.urls import reverse
from constance import config


@pytest.fixture
def logged_client(client, django_user_model):
    config.SOPDS_AUTH = True
    user = django_user_model.objects.create_user('bs', 'b@x.y', 'pw')
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_bsadd_without_referer_redirects_to_main(logged_client):
    resp = logged_client.get(reverse('web:bsadd'))   # no ?book, no Referer
    assert resp.status_code == 302
    assert resp['Location'] == reverse('web:main')
