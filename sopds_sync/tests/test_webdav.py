"""Coverage for the Moon+ Reader WebDAV endpoint.

Exercises the feature toggle, HTTP Basic auth, the file round-trip that Moon+
Reader relies on (PUT/GET/PROPFIND/DELETE/MKCOL/MOVE), and per-user isolation
plus path-traversal containment.
"""
import base64
import os

import pytest
from constance import config

from sopds_sync.webdav import _resolve


def basic(username='dav', password='pw123456'):
    token = base64.b64encode(('%s:%s' % (username, password)).encode()).decode()
    return {'HTTP_AUTHORIZATION': 'Basic %s' % token}


def body_bytes(response):
    if getattr(response, 'streaming', False):
        return b''.join(response.streaming_content)
    return response.content


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username='dav', password='pw123456')


@pytest.fixture(autouse=True)
def dav_env(db, tmp_path):
    config.SOPDS_WEBDAV_ENABLE = True
    config.SOPDS_WEBDAV_ROOT = str(tmp_path)
    return tmp_path


# --- feature toggle & auth -------------------------------------------------

@pytest.mark.django_db
def test_disabled_returns_404(client, user):
    config.SOPDS_WEBDAV_ENABLE = False
    r = client.generic('OPTIONS', '/dav/', **basic())
    assert r.status_code == 404


@pytest.mark.django_db
def test_requires_auth(client, user):
    r = client.generic('PROPFIND', '/dav/', **{'HTTP_DEPTH': '0'})
    assert r.status_code == 401
    assert 'Basic' in r['WWW-Authenticate']


@pytest.mark.django_db
def test_bad_password_rejected(client, user):
    r = client.generic('OPTIONS', '/dav/', **basic(password='wrong'))
    assert r.status_code == 401


@pytest.mark.django_db
def test_options_advertises_dav(client, user):
    r = client.generic('OPTIONS', '/dav/', **basic())
    assert r.status_code == 200
    assert '2' in r['DAV']
    assert 'PROPFIND' in r['Allow']


# --- file round-trip -------------------------------------------------------

@pytest.mark.django_db
def test_put_then_get(client, user):
    r = client.put('/dav/book.epub.po', data=b'55.0%', **basic())
    assert r.status_code == 201
    r2 = client.get('/dav/book.epub.po', **basic())
    assert r2.status_code == 200
    assert body_bytes(r2) == b'55.0%'


@pytest.mark.django_db
def test_put_overwrite_returns_204(client, user):
    client.put('/dav/book.po', data=b'1', **basic())
    r = client.put('/dav/book.po', data=b'2', **basic())
    assert r.status_code == 204


@pytest.mark.django_db
def test_propfind_lists_children(client, user):
    client.put('/dav/book.po', data=b'x', **basic())
    r = client.generic('PROPFIND', '/dav/', **dict(basic(), HTTP_DEPTH='1'))
    assert r.status_code == 207
    assert b'book.po' in r.content
    assert b'DAV:' in r.content


@pytest.mark.django_db
def test_propfind_depth0_self_only(client, user):
    client.put('/dav/book.po', data=b'x', **basic())
    r = client.generic('PROPFIND', '/dav/', **dict(basic(), HTTP_DEPTH='0'))
    assert r.status_code == 207
    assert b'book.po' not in r.content


@pytest.mark.django_db
def test_mkcol_and_delete(client, user):
    assert client.generic('MKCOL', '/dav/backup', **basic()).status_code == 201
    assert client.put('/dav/backup/a.po', data=b'x', **basic()).status_code == 201
    assert client.delete('/dav/backup', **basic()).status_code == 204
    assert client.get('/dav/backup/a.po', **basic()).status_code == 404


@pytest.mark.django_db
def test_move(client, user):
    client.put('/dav/old.po', data=b'pos', **basic())
    r = client.generic('MOVE', '/dav/old.po',
                       **dict(basic(), HTTP_DESTINATION='http://testserver/dav/new.po'))
    assert r.status_code == 201
    assert client.get('/dav/old.po', **basic()).status_code == 404
    assert body_bytes(client.get('/dav/new.po', **basic())) == b'pos'


@pytest.mark.django_db
def test_get_missing_404(client, user):
    assert client.get('/dav/nope.po', **basic()).status_code == 404


# --- isolation & safety ----------------------------------------------------

@pytest.mark.django_db
def test_per_user_isolation(client, user, django_user_model):
    django_user_model.objects.create_user(username='other', password='pw123456')
    client.put('/dav/secret.po', data=b'mine', **basic())
    # The other user's namespace is a different directory: file not visible.
    assert client.get('/dav/secret.po', **basic('other')).status_code == 404


def test_resolve_contains_traversal(tmp_path):
    root = os.path.join(str(tmp_path), '1')
    os.makedirs(root)
    escaped = _resolve(root, '../../../../etc/passwd')
    assert escaped is not None
    # The traversal is anchored inside the user's root, never above it.
    assert escaped == os.path.join(root, 'etc', 'passwd')
    assert _resolve(root, 'sub/dir/file.po') == os.path.join(root, 'sub', 'dir', 'file.po')
