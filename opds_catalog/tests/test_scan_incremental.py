"""Incremental scan: a second scan skips unchanged archives and does not
duplicate books (#51 coverage gap; arc_skip / avail lifecycle).
"""
import os

import pytest
from constance import config

from opds_catalog import opdsdb
from opds_catalog.sopdscan import opdsScanner
from opds_catalog.models import Book

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


@pytest.mark.django_db
def test_second_scan_skips_unchanged_archive_and_keeps_counts():
    config.SOPDS_ROOT_LIB = DATA
    config.SOPDS_INPX_ENABLE = False
    opdsdb.clear_all()

    first = opdsScanner()
    first.scan_all()
    count_after_first = Book.objects.count()
    assert count_after_first > 0

    second = opdsScanner()
    second.scan_all()

    assert Book.objects.count() == count_after_first   # no duplicates
    assert second.arch_skipped >= 1                     # books.zip unchanged -> skipped
    assert second.books_added == 0
