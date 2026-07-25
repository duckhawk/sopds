"""The env-gated security settings helper (#42)."""
from sopds.settings import _env_bool


def test_env_bool_truthy(monkeypatch):
    for v in ('1', 'true', 'TRUE', 'yes', 'Yes'):
        monkeypatch.setenv('SEC_FLAG', v)
        assert _env_bool('SEC_FLAG') is True


def test_env_bool_falsy(monkeypatch):
    for v in ('0', 'false', 'no', ''):
        monkeypatch.setenv('SEC_FLAG', v)
        assert _env_bool('SEC_FLAG') is False


def test_env_bool_default(monkeypatch):
    monkeypatch.delenv('SEC_FLAG', raising=False)
    assert _env_bool('SEC_FLAG') is False
    assert _env_bool('SEC_FLAG', default=True) is True
