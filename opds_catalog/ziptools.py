"""Thin wrapper around the standard library :mod:`zipfile` that decodes legacy
(non-UTF-8) member names with the configured codepage (default cp866) instead
of the stdlib cp437 fallback.

Russian book collections (flibusta/librusec dumps) store cyrillic filenames as
cp866 bytes without the UTF-8 flag (bit 0x800). The stdlib decodes those as
cp437 -> mojibake, which breaks scanning and downloads. This module recovers
the intended name (``bytes.decode('cp437').encode('cp437').decode(cp866)``) and
fixes both ``ZipInfo.filename`` and the ``NameToInfo`` map, so ``namelist()``,
``open(name)`` and ``getinfo(name)`` all work with the correct names — letting
us use stdlib ``zipfile`` instead of a 1900-line vendored fork.
"""
import zipfile

from constance import config

# Re-export for callers that used the old `opds_catalog.zipf` alias.
BadZipFile = zipfile.BadZipFile
ZIP_DEFLATED = zipfile.ZIP_DEFLATED
ZipFile = zipfile.ZipFile


def open_zipfile(file):
    """Open ``file`` for reading, correcting legacy member-name encodings."""
    z = zipfile.ZipFile(file, 'r', allowZip64=True)
    codepage = getattr(config, 'SOPDS_ZIPCODEPAGE', None) or 'cp866'
    if codepage.lower() in ('cp437', 'ascii', ''):
        return z
    remap = {}
    for zi in z.infolist():
        if not (zi.flag_bits & 0x800):  # no UTF-8 flag -> stdlib used cp437
            try:
                zi.filename = zi.filename.encode('cp437').decode(codepage)
            except (UnicodeEncodeError, UnicodeDecodeError, LookupError):
                pass
        remap[zi.filename] = zi
    z.NameToInfo = remap
    return z
