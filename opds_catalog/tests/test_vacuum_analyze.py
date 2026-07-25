"""opdsdb.vacuum_analyze() is a safe no-op on non-PostgreSQL backends."""
import pytest

from opds_catalog import opdsdb


@pytest.mark.django_db
def test_vacuum_analyze_noop_on_sqlite():
    # The test DB is sqlite; vacuum_analyze must return without raising.
    opdsdb.vacuum_analyze()
