"""Happy-path coverage for the download/read/cover views and dl helpers,
using the real sample FB2 in tests/data."""
import os

import pytest
from django.urls import reverse
from constance import config

from opds_catalog.models import Book, Catalog

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FB2 = '262001.fb2'


@pytest.fixture
def library(db):
    config.SOPDS_ROOT_LIB = DATA
    cat = Catalog.objects.create(parent=None, cat_name='.', path='.', cat_type=0)
    return Book.objects.create(
        filename=FB2, path='.', filesize=os.path.getsize(os.path.join(DATA, FB2)),
        format='fb2', cat_type=0, docdate='2011', lang='en',
        title='The Sanctuary Sparrow', search_title='THE SANCTUARY SPARROW',
        annotation='', avail=2, catalog=cat,
    )


@pytest.fixture
def logged_client(client, django_user_model):
    user = django_user_model.objects.create_user(username='dluser', password='pw')
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_download_original(logged_client, library):
    resp = logged_client.get(reverse('opds:download', args=[library.id, 0]))
    assert resp.status_code == 200
    assert int(resp['Content-Length']) > 0
    assert 'attachment' in resp['Content-Disposition']


@pytest.mark.django_db
def test_download_zip(logged_client, library):
    resp = logged_client.get(reverse('opds:download', args=[library.id, 1]))
    assert resp.status_code == 200
    assert resp['Content-Disposition'].rstrip('"').endswith('.zip')


@pytest.mark.django_db
def test_read_fb2_renders_html(logged_client, library):
    resp = logged_client.get(reverse('opds:read', args=[library.id]))
    assert resp.status_code == 200
    assert resp['Content-Type'].startswith('text/html')
    assert b'<' in resp.content


@pytest.mark.django_db
def test_cover_returns_image(logged_client, library):
    resp = logged_client.get(reverse('opds:cover', args=[library.id]))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_dl_helpers(library):
    from opds_catalog import dl
    config.SOPDS_ROOT_LIB = DATA
    name = dl.getFileName(library)
    assert name.endswith('.fb2')

    data = dl.getFileData(library)
    assert data is not None
    assert len(data.read()) > 0

    zipped = dl.getFileDataZip(library)
    assert zipped is not None
    assert len(zipped.read()) > 0
