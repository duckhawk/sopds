"""Tests for opds_catalog.ziptools.open_zipfile — the stdlib-zipfile wrapper
that replaced the vendored zipf.py. Covers the normal (ASCII/UTF-8) path and,
crucially, the cp866 legacy path used by Russian book archives.
"""
import io
import os
import zipfile

import pytest

from opds_catalog.ziptools import open_zipfile

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def _make_cp866_zip(name, content):
    """Build a zip whose single member is stored as cp866 bytes WITHOUT the
    UTF-8 flag (as legacy Windows archivers do). Uses an equal-length ASCII
    placeholder then swaps the bytes, so no UTF-8 flag is set."""
    raw = name.encode('cp866')
    placeholder = b'a' * len(raw)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo(placeholder.decode('ascii')), content)
    return buf.getvalue().replace(placeholder, raw)


@pytest.mark.django_db
def test_open_zipfile_ascii_names():
    z = open_zipfile(os.path.join(DATA, 'books.zip'))
    assert z.namelist() == ["539603.fb2", "539485.fb2", "539273.fb2"]
    assert z.getinfo("539485.fb2").file_size == 12293
    assert z.open("539485.fb2").read(38) == b'<?xml version="1.0" encoding="utf-8"?>'


@pytest.mark.django_db
def test_open_zipfile_bad_zip():
    with pytest.raises(zipfile.BadZipFile):
        open_zipfile(os.path.join(DATA, 'badfile.zip'))


@pytest.mark.django_db
def test_open_zipfile_cp866_names():
    blob = _make_cp866_zip('книга.fb2', b'<FictionBook/>')
    z = open_zipfile(io.BytesIO(blob))
    # Name is decoded from cp866, not the stdlib cp437 mojibake.
    assert z.namelist() == ['книга.fb2']
    # And it is openable / stat-able by that decoded name.
    assert z.getinfo('книга.fb2').file_size == len(b'<FictionBook/>')
    assert z.open('книга.fb2').read() == b'<FictionBook/>'
