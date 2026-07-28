"""The Django admin's own chrome, which this project only rebrands.

`admin/base.html` used to be overridden here with a copy of the template from
Django 1.x, which shadowed the stock one entirely. That copy logged out with a
link, and `LogoutView` has answered anything but POST with 405 since Django 5.0
— so the only way out of the admin was an error page. The override is gone;
these tests keep it from coming back and check that the rebranding in
`admin/base_site.html`, which is a legitimate override, still lands.
"""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse


@pytest.fixture
def admin_client(client, db):
    User.objects.create_superuser('root', 'root@example.com', 'pw')
    client.login(username='root', password='pw')
    return client


@pytest.mark.django_db
def test_admin_logs_out_by_post(admin_client):
    page = admin_client.get(reverse('admin:index')).content.decode()
    assert '<form id="logout-form" method="post"' in page
    assert 'href="%s"' % reverse('admin:logout') not in page

    resp = admin_client.post(reverse('admin:logout'))
    assert resp.status_code == 200
    assert '_auth_user_id' not in admin_client.session


@pytest.mark.django_db
def test_admin_keeps_the_branding(admin_client):
    page = admin_client.get(reverse('admin:index')).content.decode()
    assert 'Lectern administration' in page
