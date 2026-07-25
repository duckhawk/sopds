"""Coverage for the KOReader (kosync) progress-sync API.

Exercises the feature toggle, header authentication against KosyncCredential,
the progress key-value round-trip (last write wins), and the optional
self-registration path.
"""
import hashlib
import json

import pytest
from constance import config

from sopds_sync.models import KosyncCredential, KosyncProgress


def md5(s):
    return hashlib.md5(s.encode('utf-8')).hexdigest()


def hdr(username='reader', password='syncpass'):
    return {'HTTP_X_AUTH_USER': username, 'HTTP_X_AUTH_KEY': md5(password)}


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username='reader', password='pw')


@pytest.fixture
def cred(user):
    c = KosyncCredential(user=user)
    c.set_password('syncpass')
    c.save()
    return c


@pytest.fixture(autouse=True)
def enable_kosync(db):
    config.SOPDS_KOSYNC_ENABLE = True
    config.SOPDS_KOSYNC_ALLOW_REGISTER = False


# --- feature toggle --------------------------------------------------------

@pytest.mark.django_db
def test_disabled_returns_404(client, cred):
    config.SOPDS_KOSYNC_ENABLE = False
    r = client.get('/kosync/users/auth', **hdr())
    assert r.status_code == 404


# --- authentication --------------------------------------------------------

@pytest.mark.django_db
def test_auth_ok(client, cred):
    r = client.get('/kosync/users/auth', **hdr())
    assert r.status_code == 200
    assert r.json()['authorized'] == 'OK'


@pytest.mark.django_db
def test_auth_wrong_key(client, cred):
    r = client.get('/kosync/users/auth', **hdr(password='nope'))
    assert r.status_code == 401


@pytest.mark.django_db
def test_auth_no_credential(client, user):
    r = client.get('/kosync/users/auth', **hdr())
    assert r.status_code == 401


@pytest.mark.django_db
def test_auth_missing_headers(client, cred):
    assert client.get('/kosync/users/auth').status_code == 401


# --- progress store --------------------------------------------------------

@pytest.mark.django_db
def test_progress_roundtrip(client, cred):
    doc = 'a' * 32
    body = {'document': doc, 'progress': '/body/DocFragment[3]/body/p[5]',
            'percentage': 0.42, 'device': 'Kobo', 'device_id': 'xyz'}
    r = client.put('/kosync/syncs/progress', data=json.dumps(body),
                   content_type='application/json', **hdr())
    assert r.status_code == 200
    assert r.json()['document'] == doc

    r2 = client.get('/kosync/syncs/progress/%s' % doc, **hdr())
    assert r2.status_code == 200
    d = r2.json()
    assert d['progress'] == body['progress']
    assert abs(d['percentage'] - 0.42) < 1e-6
    assert d['device'] == 'Kobo'
    assert d['device_id'] == 'xyz'


@pytest.mark.django_db
def test_progress_last_write_wins(client, cred):
    doc = 'b' * 32
    for pct in (0.1, 0.5):
        client.put('/kosync/syncs/progress',
                   data=json.dumps({'document': doc, 'progress': 'p', 'percentage': pct}),
                   content_type='application/json', **hdr())
    assert KosyncProgress.objects.filter(user=cred.user, document=doc).count() == 1
    r = client.get('/kosync/syncs/progress/%s' % doc, **hdr())
    assert abs(r.json()['percentage'] - 0.5) < 1e-6


@pytest.mark.django_db
def test_progress_isolated_per_user(client, cred, django_user_model):
    other = django_user_model.objects.create_user(username='other', password='pw')
    oc = KosyncCredential(user=other)
    oc.set_password('otherpass')
    oc.save()
    doc = 'c' * 32
    client.put('/kosync/syncs/progress',
               data=json.dumps({'document': doc, 'progress': 'mine', 'percentage': 0.9}),
               content_type='application/json', **hdr())
    # The other user sees no progress for the same document hash.
    r = client.get('/kosync/syncs/progress/%s' % doc, **hdr('other', 'otherpass'))
    assert r.json() == {}


@pytest.mark.django_db
def test_progress_requires_auth(client, cred):
    assert client.get('/kosync/syncs/progress/%s' % ('c' * 32)).status_code == 401


@pytest.mark.django_db
def test_get_missing_returns_empty(client, cred):
    r = client.get('/kosync/syncs/progress/%s' % ('d' * 32), **hdr())
    assert r.status_code == 200
    assert r.json() == {}


@pytest.mark.django_db
def test_put_rejects_bad_document(client, cred):
    r = client.put('/kosync/syncs/progress',
                   data=json.dumps({'document': 'not-a-hash!', 'progress': 'p', 'percentage': 0.1}),
                   content_type='application/json', **hdr())
    assert r.status_code == 400


# --- self-registration -----------------------------------------------------

@pytest.mark.django_db
def test_register_disabled_by_default(client, user):
    r = client.post('/kosync/users/create',
                    data=json.dumps({'username': 'reader', 'password': md5('x')}),
                    content_type='application/json')
    assert r.status_code == 403


@pytest.mark.django_db
def test_register_creates_credential_when_allowed(client, user):
    config.SOPDS_KOSYNC_ALLOW_REGISTER = True
    key = md5('newpass')
    r = client.post('/kosync/users/create',
                    data=json.dumps({'username': 'reader', 'password': key}),
                    content_type='application/json')
    assert r.status_code == 201
    assert KosyncCredential.objects.get(user=user).auth_key == key
    # A second attempt for an already-registered user is rejected.
    r2 = client.post('/kosync/users/create',
                     data=json.dumps({'username': 'reader', 'password': key}),
                     content_type='application/json')
    assert r2.status_code == 402


@pytest.mark.django_db
def test_register_rejects_unknown_user(client):
    config.SOPDS_KOSYNC_ALLOW_REGISTER = True
    r = client.post('/kosync/users/create',
                    data=json.dumps({'username': 'ghost', 'password': md5('x')}),
                    content_type='application/json')
    assert r.status_code == 402


@pytest.mark.django_db
def test_register_refuses_staff(client, django_user_model):
    config.SOPDS_KOSYNC_ALLOW_REGISTER = True
    django_user_model.objects.create_user(username='boss', password='pw', is_staff=True)
    r = client.post('/kosync/users/create',
                    data=json.dumps({'username': 'boss', 'password': md5('x')}),
                    content_type='application/json')
    assert r.status_code == 402
    assert not KosyncCredential.objects.filter(user__username='boss').exists()
