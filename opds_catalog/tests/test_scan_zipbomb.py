"""processzip() must skip a zip member that declares more than the size cap
instead of decompressing it (#39 decompression bomb guard).
"""
import zipfile

import pytest
from constance import config

import opds_catalog.sopdscan as sopdscan
from opds_catalog import opdsdb
from opds_catalog.sopdscan import opdsScanner
from opds_catalog.models import Book


@pytest.mark.django_db
def test_processzip_skips_oversized_member(tmp_path, monkeypatch):
    monkeypatch.setattr(sopdscan, 'MAX_BOOK_BYTES', 1000)
    zip_path = tmp_path / 'books.zip'
    with zipfile.ZipFile(zip_path, 'w') as z:
        z.writestr('big.fb2', b'x' * 5000)   # declared size well over the cap

    config.SOPDS_ROOT_LIB = str(tmp_path)
    opdsdb.clear_all()
    scanner = opdsScanner()
    scanner.processzip('books.zip', str(tmp_path), str(zip_path))   # must not OOM/crash

    assert scanner.books_added == 0
    assert Book.objects.count() == 0
