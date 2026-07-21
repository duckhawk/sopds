"""Test settings: inherit production defaults but force a fast, isolated DB.

Used by pytest (see pytest.ini). Overrides the DB to an in-memory sqlite
regardless of any MYSQL_*/POSTGRES_* environment variables that may be present
in the developer's or CI shell.
"""
from sopds.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Deterministic key for tests; never used in production.
SECRET_KEY = 'test-only-secret-key'

# Faster password hashing in tests.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
