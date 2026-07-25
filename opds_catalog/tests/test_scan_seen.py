"""Incremental-scan `avail` redesign: the seen-set sweep (opdsdb.scan_*).

A book that vanished from disk is deleted on the next scan; an empty seen set
never wipes the catalogue; logical mode marks avail=0 instead of deleting.
"""
import os
import shutil

import pytest
from constance import config

from opds_catalog import opdsdb
from opds_catalog.sopdscan import opdsScanner
from opds_catalog.models import Book

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def _addbook(name):
    cat = opdsdb.addcattree('.')
    return opdsdb.addbook(name, '.', cat, 'fb2', 'T ' + name, '', '2020', 'en', 1, 0)


@pytest.mark.django_db
def test_rescan_deletes_a_book_removed_from_disk(tmp_path):
    lib = tmp_path / 'lib'
    lib.mkdir()
    shutil.copy(os.path.join(DATA, '262001.fb2'), lib / 'a.fb2')
    shutil.copy(os.path.join(DATA, '262001.fb2'), lib / 'b.fb2')

    config.SOPDS_ROOT_LIB = str(lib)
    config.SOPDS_INPX_ENABLE = False
    config.SOPDS_DELETE_LOGICAL = False
    opdsdb.clear_all()

    opdsScanner().scan_all()
    assert Book.objects.count() == 2

    (lib / 'b.fb2').unlink()          # book vanished from disk
    scanner = opdsScanner()
    scanner.scan_all()

    assert Book.objects.count() == 1
    assert Book.objects.filter(filename='a.fb2').exists()
    assert not Book.objects.filter(filename='b.fb2').exists()
    assert scanner.books_deleted == 1


@pytest.mark.django_db
def test_scan_finish_empty_seen_does_not_wipe_catalogue():
    opdsdb.clear_all()
    _addbook('x.fb2')                 # creates the book AND marks it seen
    opdsdb.scan_begin()               # clear the seen set

    deleted = opdsdb.scan_finish()    # seen empty + catalogue non-empty -> guard
    assert deleted == 0
    assert Book.objects.count() == 1


@pytest.mark.django_db
def test_scan_finish_physical_and_logical():
    opdsdb.clear_all()
    b1 = _addbook('a.fb2')
    b2 = _addbook('b.fb2')

    # Only b1 is seen this pass.
    opdsdb.scan_begin()
    opdsdb.mark_seen(b1.id)

    # Logical: b2 marked avail=0, not deleted.
    assert opdsdb.scan_finish(logical=True) == 1
    assert Book.objects.count() == 2
    b2.refresh_from_db(); assert b2.avail == 0
    b1.refresh_from_db(); assert b1.avail == 2

    # Physical: b2 removed.
    opdsdb.scan_begin()
    opdsdb.mark_seen(b1.id)
    assert opdsdb.scan_finish(logical=False) == 1
    assert Book.objects.count() == 1
    assert Book.objects.filter(id=b1.id).exists()
