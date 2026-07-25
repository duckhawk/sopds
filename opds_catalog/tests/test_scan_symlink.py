"""scan_all() must not loop forever on a symlink cycle (#48).

os.walk(followlinks=True) recurses indefinitely when a directory contains a
symlink to one of its ancestors; the scanner now prunes already-visited real
paths.
"""
import os
import shutil

import pytest
from constance import config

from opds_catalog import opdsdb
from opds_catalog.sopdscan import opdsScanner
from opds_catalog.models import Book

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


@pytest.mark.django_db
def test_scan_all_breaks_symlink_cycle(tmp_path):
    lib = tmp_path / 'lib'
    sub = lib / 'sub'
    sub.mkdir(parents=True)
    shutil.copy(os.path.join(DATA, '262001.fb2'), lib / 'book.fb2')
    # sub/loop -> lib : os.walk would otherwise recurse lib/sub/loop/sub/... forever.
    os.symlink(lib, sub / 'loop')

    config.SOPDS_ROOT_LIB = str(lib)
    config.SOPDS_INPX_ENABLE = False

    opdsdb.clear_all()
    scanner = opdsScanner()
    scanner.scan_all()   # must terminate

    # The single real book is catalogued exactly once despite the cycle.
    assert scanner.books_added == 1
    assert Book.objects.count() == 1
