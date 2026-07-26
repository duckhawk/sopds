# -*- coding: utf-8 -*-
"""Books that are pages of images rather than a stream of text.

FB2 and EPUB are reflowable: the reader turns them into one long numbered
sequence of paragraphs and the browser lays it out. A PDF or a DjVu scan has no
such thing — the page *is* the unit, its layout is fixed, and the only honest
way to show it is to draw it. So these formats get their own reader, and this
module supplies it with the one thing it understands: a PDF.

PDF is served as it is. DjVu is converted with `ddjvu`, which every DjVu
installation ships and which is invoked at arm's length as a separate program,
exactly as the FB2 converters already are. The result is cached on disk rather
than in the shared cache: a converted scan is tens of megabytes, which is not
something to push through Redis, and the conversion is far too slow to repeat on
every page turn.

The module deliberately does not import `dl`, which imports it — the containing
file's path is passed in instead.
"""
import functools
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import zipfile

from django.http import HttpResponse, StreamingHttpResponse
from constance import config

from opds_catalog import opdsdb
from opds_catalog.ziptools import open_zipfile

logger = logging.getLogger(__name__)

# Formats the paged reader can show. PDF needs nothing; DjVu needs a converter,
# so `viewable_formats()` is what the rest of the code should ask.
PAGED_FORMATS = ('pdf', 'djvu')

# Conversion happens while the reader waits, so the ceiling is the deployment's
# own request budget — uwsgi's harakiri is 120s — and this sits just inside it,
# to fail with a logged 404 rather than have the worker killed from under us.
#
# Measured on djvulibre 3.5.28: a bitonal A4 page converts in about 10ms and a
# photographic one in up to 200ms, so this covers several thousand pages of text
# or several hundred of plates. A scan larger than that needs both this and the
# request budget raised; it is converted once and cached, so it is a one-off.
CONVERT_TIMEOUT = 110

# How much converted output to keep on disk. Nothing here is precious — every
# file can be produced again from the book — so the cache is pruned oldest-first
# once it grows past this.
CACHE_BYTES = 512 * 1024 * 1024

# Serving is chunked so a 200 MB scan does not become 200 MB of resident memory.
CHUNK_BYTES = 256 * 1024

_RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')


def is_paged(book_format):
    return (book_format or '').lower() in PAGED_FORMATS


@functools.lru_cache(maxsize=8)
def _converter_argv(command):
    """The converter command as an argv, or None if it is not runnable.

    Keyed on the configured string so changing the setting in the admin takes
    effect at once, and cached because this is asked once per row of a book
    listing and would otherwise be a `PATH` walk each time.
    """
    argv = shlex.split(command or '')
    if not argv:
        return None
    if shutil.which(argv[0]) is None:
        logger.warning('DjVu converter %r is not on PATH; DjVu books cannot be read',
                       argv[0])
        return None
    return argv


def djvu_converter():
    return _converter_argv(config.SOPDS_DJVUTOPDF)


def viewable_formats():
    """The paged formats this installation can actually show.

    DjVu depends on a converter being installed. Offering to read one that
    cannot be converted produces a reader that never loads, so a deployment
    without `ddjvu` simply does not advertise DjVu as readable.
    """
    if djvu_converter() is None:
        return ('pdf',)
    return PAGED_FORMATS


def cache_dir():
    return os.path.join(config.SOPDS_TEMP_DIR, 'paged')


def _prune(directory, limit=CACHE_BYTES):
    """Keep the cache under `limit`, discarding least recently written first."""
    try:
        entries = []
        total = 0
        with os.scandir(directory) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                st = entry.stat()
                entries.append((st.st_mtime, st.st_size, entry.path))
                total += st.st_size

        for _mtime, size, path in sorted(entries):
            if total <= limit:
                break
            os.unlink(path)
            total -= size
    except OSError as err:
        # A full or unwritable cache directory must not take the reader down.
        logger.warning('Cannot prune the paged-reader cache: %s', err)


def _publish(tmp_path, final_path):
    """Move a freshly built file into the cache, atomically.

    Several workers can be converting the same book at the same moment; each
    writes its own temporary file and the last rename wins, so a reader never
    sees a half-written PDF.
    """
    os.replace(tmp_path, final_path)
    _prune(os.path.dirname(final_path))


def _extract(container, member, destination):
    """Copy one member out of a zip without holding it all in memory."""
    with open(container, 'rb') as raw, open_zipfile(raw) as archive:
        with archive.open(member) as src, open(destination, 'wb') as dst:
            shutil.copyfileobj(src, dst, CHUNK_BYTES)


def _convert(source, destination):
    """Run the DjVu converter. False if it is missing, fails or times out."""
    argv = djvu_converter()
    if argv is None:
        return False

    try:
        proc = subprocess.run(argv + [source, destination],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=CONVERT_TIMEOUT)
    except subprocess.TimeoutExpired:
        logger.warning('DjVu conversion of %s timed out after %ss', source, CONVERT_TIMEOUT)
        return False
    except OSError as err:
        logger.warning('Cannot run the DjVu converter: %s', err)
        return False

    if proc.returncode != 0:
        logger.warning('DjVu conversion of %s failed (%s): %s',
                       source, proc.returncode, proc.stderr[:500].decode('utf-8', 'replace'))
        return False

    # ddjvu is content with an unreadable page and still exits 0, leaving
    # nothing behind. An empty file is not a PDF either.
    return os.path.exists(destination) and os.path.getsize(destination) > 0


def materialise(book, validator, container):
    """Filesystem path of a PDF holding `book`, or None if it cannot be made.

    `container` is the file that physically holds the book — the book itself
    for a plain catalogue entry, the enclosing zip otherwise. A plain PDF is
    served straight from the collection and never copied; everything else is
    built once and kept under `cache_dir()`, keyed on the same content
    validator as the ETag, so replacing a file on disk invalidates it.
    """
    if book.format == 'pdf' and book.cat_type == opdsdb.CAT_NORMAL:
        return container if os.path.exists(container) else None

    if validator is None:           # the book file is gone; nothing to key on
        return None

    directory = cache_dir()
    cached = os.path.join(directory, '%s.pdf' % validator.strip('"'))
    if os.path.exists(cached):
        # Touch it so the prune treats "recently read" as "worth keeping".
        try:
            os.utime(cached, None)
        except OSError:
            pass
        return cached

    os.makedirs(directory, exist_ok=True)
    try:
        return _build(book, container, directory, cached)
    except (zipfile.BadZipFile, KeyError, OSError) as err:
        logger.warning('Cannot prepare book %s for the paged reader: %s', book.id, err)
        return None


def _build(book, container, directory, cached):
    """Produce the cached PDF for a book that is not a plain PDF on disk.

    That leaves a PDF stored inside a zip, which only has to be unpacked, and a
    DjVu, which has to be converted — and unpacked first if it is zipped too,
    because the converter is a separate program that reads a file.
    """
    with tempfile.TemporaryDirectory(dir=directory) as work:
        source = container
        if book.cat_type in (opdsdb.CAT_ZIP, opdsdb.CAT_INP):
            source = os.path.join(work, 'source')
            _extract(container, book.filename, source)

        if book.format == 'pdf':
            _publish(source, cached)
            return cached

        output = os.path.join(work, 'out.pdf')
        if not _convert(source, output):
            return None
        _publish(output, cached)
        return cached


def _stream(path, start, length):
    with open(path, 'rb') as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(CHUNK_BYTES, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


def serve(request, path, filename):
    """Send a PDF, honouring `Range`.

    pdf.js asks for the trailer, then the cross-reference table, then the pages
    the reader actually looks at. Answering those with partial responses is the
    difference between opening a 300 MB scan instantly and downloading all of it
    before the first page appears — and Django serves whole files only, so the
    header is handled here.
    """
    size = os.path.getsize(path)
    start, end, status = 0, size - 1, 200

    match = _RANGE.match((request.headers.get('Range') or '').strip())
    if match and size:
        first, last = match.group(1), match.group(2)
        if first:
            start = int(first)
            if last:
                end = min(int(last), size - 1)
        elif last:
            start = max(0, size - int(last))     # a suffix range: the last N bytes
        else:
            match = None                          # "bytes=-" means nothing

    if match and size:
        if start >= size or start > end:
            refused = HttpResponse(status=416)
            refused['Content-Range'] = 'bytes */%d' % size
            return refused
        status = 206

    length = end - start + 1 if size else 0
    response = StreamingHttpResponse(_stream(path, start, length), status=status,
                                     content_type='application/pdf')
    response['Content-Length'] = str(length)
    response['Accept-Ranges'] = 'bytes'
    # inline: the reader draws it in a canvas, and a download prompt in the
    # middle of opening a book is not what was asked for.
    response['Content-Disposition'] = 'inline; filename="%s"' % filename
    if status == 206:
        response['Content-Range'] = 'bytes %d-%d/%d' % (start, end, size)
    return response
