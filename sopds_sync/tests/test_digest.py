"""KOReader document digests.

The offsets in `SAMPLE_OFFSETS` come out of a LuaJIT quirk (`bit.lshift` masks
the shift count to five bits and truncates to 32 bits, so KOReader's `i = -1`
iteration asks for `1024 << 30` and lands on offset 0). Nothing in the Lua
source says so, so it is pinned here: getting it wrong would not raise anything,
it would just silently stop matching every book on every device.

The values below were checked against the hashes in KOReader's own
`spec/unit/util_spec.lua`, using the fixtures from github.com/koreader/test-data:

    tall.pdf     41cce710f34e5ec21315e19c99821415
    leaves.epub  59d481d168cca6267322f150c5f6a2a3

Those files are ~1.2 MB together, so instead of vendoring them the tests below
rebuild the same guarantees from a generated buffer.
"""
import hashlib
import io

from sopds_sync.digest import SAMPLE_OFFSETS, filename_md5, partial_md5

# 1024 << (2i) for i in -1..10, with the 32-bit wraparound on the first one.
EXPECTED_OFFSETS = (
    0,           # i = -1: 1024 << 30, truncated to 32 bits
    1024,        # i = 0
    4096, 16384, 65536, 262144, 1048576,
    4194304, 16777216, 67108864, 268435456, 1073741824,
)


def buffer_of(size):
    """A deterministic, non-repeating buffer, so a wrong offset changes the hash."""
    return bytes((i * 37 + (i >> 8)) & 0xFF for i in range(size))


def test_sample_offsets_are_the_koreader_ones():
    assert SAMPLE_OFFSETS == EXPECTED_OFFSETS


def test_partial_md5_hashes_exactly_the_sampled_windows():
    data = buffer_of(300000)

    expected = hashlib.md5()
    for offset in EXPECTED_OFFSETS:
        window = data[offset:offset + 1024]
        if not window:
            break
        expected.update(window)

    assert partial_md5(io.BytesIO(data)) == expected.hexdigest()


def test_partial_md5_reads_the_head_of_the_file():
    """The i=-1 wraparound: a file shorter than 1024 bytes still hashes its
    contents rather than nothing."""
    data = b'short book'
    assert partial_md5(io.BytesIO(data)) == hashlib.md5(data).hexdigest()


def test_partial_md5_stops_at_the_first_offset_past_the_end():
    data = buffer_of(2000)   # covers offsets 0 and 1024, not 4096
    expected = hashlib.md5(data[0:1024] + data[1024:2048]).hexdigest()
    assert partial_md5(io.BytesIO(data)) == expected


def test_partial_md5_of_an_empty_file():
    assert partial_md5(io.BytesIO(b'')) == hashlib.md5(b'').hexdigest()


def test_a_changed_byte_in_a_sampled_window_changes_the_digest():
    data = bytearray(buffer_of(300000))
    before = partial_md5(io.BytesIO(bytes(data)))
    data[262144] ^= 0xFF
    assert partial_md5(io.BytesIO(bytes(data))) != before


def test_a_changed_byte_between_windows_does_not():
    """A partial hash is exactly that — the point is that it is cheap, not that
    it is a full checksum."""
    data = bytearray(buffer_of(300000))
    before = partial_md5(io.BytesIO(bytes(data)))
    data[3000] ^= 0xFF          # between the 1024 and 4096 windows
    assert partial_md5(io.BytesIO(bytes(data))) == before


def test_filename_md5_hashes_the_base_name_only():
    assert filename_md5('/books/sci-fi/Dune.fb2') == filename_md5('Dune.fb2')
    assert filename_md5('Dune.fb2') == hashlib.md5(b'Dune.fb2').hexdigest()


def test_filename_md5_of_nothing_is_empty():
    assert filename_md5('') == ''
    assert filename_md5(None) == ''
