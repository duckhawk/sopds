"""KOReader document digests, so a kosync `document` can name a catalogue Book.

kosync identifies a book only by an opaque 32-char hash the client computes, and
KOReader offers two ways of computing it (Settings -> Progress sync -> Document
matching method):

* **filename** — ``md5`` of the file's base name.
* **binary** — a *partial* md5: twelve 1 KiB samples at exponentially spaced
  offsets, so it survives a rename and costs almost nothing to compute.

Reimplementing the binary one needs care, because the offsets come out of a
LuaJIT quirk rather than from any intent that is visible in the source::

    for i = -1, 10 do
        file:seek("set", lshift(step, 2*i))    -- step = 1024

``bit.lshift`` masks the shift count to five bits and truncates the result to 32
bits, so ``i = -1`` asks for ``1024 << 30``, which wraps to **0** — the first
sample is the head of the file, not something past its end. The rest are
``1024 << 2i``. `test_digest.py` pins this against the hashes in KOReader's own
`spec/unit/util_spec.lua`, so a future rewrite cannot quietly break matching.

Upstream: `util.partialMD5` in frontend/util.lua, `KOSync:getDocumentDigest`
in plugins/kosync.koplugin/main.lua.
"""
import hashlib
import os

SAMPLE_SIZE = 1024
STEP = 1024

#: Byte offsets sampled by the binary method, in order.
SAMPLE_OFFSETS = tuple(
    (STEP << ((2 * i) & 31)) & 0xFFFFFFFF for i in range(-1, 11)
)


def partial_md5(fileobj):
    """KOReader's "binary" document digest for an open, seekable binary file.

    Sampling stops at the first offset that reads nothing, mirroring the `break`
    in the Lua original — a short file is hashed from the samples it does have.
    """
    digest = hashlib.md5()
    for offset in SAMPLE_OFFSETS:
        fileobj.seek(offset)
        sample = fileobj.read(SAMPLE_SIZE)
        if not sample:
            break
        digest.update(sample)
    return digest.hexdigest()


def filename_md5(name):
    """KOReader's "filename" document digest: md5 of the base name.

    KOReader hashes the name as the bytes it reads off the filesystem; the names
    we generate are transliterated to ASCII, so utf-8 is exact for them.
    """
    base = os.path.basename(name or '')
    if not base:
        return ''
    return hashlib.md5(base.encode('utf-8')).hexdigest()
