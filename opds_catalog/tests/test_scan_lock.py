"""Cross-process scan lock (#47): a second acquisition must fail while the
first is held, so overlapping scans can't corrupt the avail-based sweep.
"""
import pytest

from opds_catalog.management.commands import sopds_scanner
from opds_catalog.management.commands.sopds_scanner import Command

pytestmark = pytest.mark.skipif(sopds_scanner.fcntl is None, reason="fcntl unavailable")


def test_scan_lock_is_exclusive(tmp_path):
    cmd = Command()
    cmd.pidfile = str(tmp_path / 's.pid')

    fd1 = cmd._acquire_lock()
    assert fd1 is not None

    fd2 = cmd._acquire_lock()      # already held -> refused
    assert fd2 is None

    cmd._release_lock(fd1)

    fd3 = cmd._acquire_lock()      # released -> available again
    assert fd3 is not None
    cmd._release_lock(fd3)
