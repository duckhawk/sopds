"""Health/readiness probes (#51 coverage gap)."""
import pytest


@pytest.mark.django_db
def test_healthz_is_ok(client):
    resp = client.get('/healthz')
    assert resp.status_code == 200
    assert resp.content == b'ok'


@pytest.mark.django_db
def test_readyz_ok_when_db_reachable(client):
    resp = client.get('/readyz')
    assert resp.status_code == 200
    assert resp.json()['status'] == 'ok'
