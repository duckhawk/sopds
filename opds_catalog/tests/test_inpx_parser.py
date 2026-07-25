"""INPX parser hardening: encoding tolerance and path-traversal guard.

Covers issues #45 (cp1251 INP records must not abort the scan) and #49 (an
untrusted FOLDER value must not escape the collection directory). The INPX
parser previously had no test coverage at all.
"""
import zipfile

import pytest
from constance import config

from opds_catalog.inpx_parser import Inpx

SEP = b'\x04'
# Default INP column order (no structure.info).
DEFAULT_COLS = 12


def _inp_line(fields):
    return SEP.join(fields) + b'\n'


def _make_inpx(path, inp_name, lines, structure=None):
    with zipfile.ZipFile(path, 'w') as z:
        if structure is not None:
            z.writestr('structure.info', structure)
        z.writestr(inp_name, b''.join(lines))


@pytest.fixture(autouse=True)
def _tests_off(db):
    config.SOPDS_INPX_TEST_ZIP = False
    config.SOPDS_INPX_TEST_FILES = False


@pytest.mark.django_db
def test_cp1251_records_do_not_raise(tmp_path):
    # 'Толстой Лев' / 'Война и мир' encoded cp1251 -> invalid utf-8 bytes.
    author = 'Толстой Лев'.encode('cp1251')
    title = 'Война и мир'.encode('cp1251')
    fields = [author, b'prose', title, b'', b'0', b'123', b'100', b'1', b'0', b'fb2', b'2020', b'ru']
    assert len(fields) == DEFAULT_COLS
    inpx = tmp_path / 'lib.inpx'
    _make_inpx(inpx, 'flibusta.inp', [_inp_line(fields)])

    captured = []
    Inpx(str(inpx), lambda f, n, meta: captured.append(meta)).parse()

    assert len(captured) == 1                       # decoded, not aborted
    assert captured[0]['FOLDER'] == 'flibusta.zip'  # auto-derived


@pytest.mark.django_db
def test_folder_path_traversal_is_skipped(tmp_path):
    cols = 'AUTHOR;GENRE;TITLE;SERIES;SERNO;FILE;SIZE;LIBID;DEL;EXT;DATE;LANG;FOLDER'

    def rec(folder):
        return [b'A', b'g', b'T', b'', b'0', b'f', b'1', b'1', b'0', b'fb2', b'2020', b'ru', folder]

    lines = [_inp_line(rec(b'../../etc/evil.zip')),
             _inp_line(rec(b'sub/ok.zip'))]
    inpx = tmp_path / 'lib.inpx'
    _make_inpx(inpx, 'meta.inp', lines, structure=cols)

    captured = []
    Inpx(str(inpx), lambda f, n, meta: captured.append(meta)).parse()

    assert len(captured) == 1                    # traversal record dropped
    assert captured[0]['FOLDER'] == 'sub/ok.zip'
