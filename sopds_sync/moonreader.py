# -*- coding: utf-8 -*-
"""The Moon+ Reader position marker: the contents of a ``.po`` file.

Moon+ Reader Pro syncs reading positions by dropping one small text file per
book into its cloud folder — with our WebDAV endpoint that lands under
``/dav/Apps/Books/.Moon+/Cache/<book file name>.po``. The whole file is a single
line::

    1784531106148*11@0#0:23.1%
    └───────────┘ └┘ │ └┘ └──┘
      device      │  │  │   percentage of the whole book
                  │  │  characters scrolled off the top of the screen,
                  │  │  counted within the chapter
                  │  volume
                  chapter: a zero-based index into the *table of contents*,
                  so prefaces and afterwords take up numbers of their own

The leading number looks like a timestamp (it is epoch milliseconds) but is the
same in every file a device writes, including files written days apart, so it
identifies the Moon+ Reader installation rather than the moment the position was
saved. Freshness therefore has to come from the file's mtime, which our WebDAV
server reports in ``getlastmodified``.

Only ``percentage`` is meaningful on its own. ``chapter`` and ``offset`` are
coordinates in Moon+ Reader's own reading of *the file on the phone*: they
depend on how it builds a table of contents and how it counts characters, and a
different edition of the same book yields different numbers. Everything here
therefore treats them as opaque unless :mod:`sopds_sync.moonpos` has confirmed
they describe the copy we hold.
"""
import re

#: ``<device>*<chapter>@<volume>#<offset>:<percentage>%``. Percent may be an
#: integer or carry one decimal; the trailing '%' is part of what Moon+ writes.
_MARKER_RE = re.compile(
    r'^\s*(?P<device>\d+)'
    r'\*(?P<chapter>\d+)'
    r'@(?P<volume>\d+)'
    r'#(?P<offset>\d+)'
    r':(?P<percent>\d+(?:\.\d+)?)%\s*$'
)

#: Everything Moon+ Reader keeps for a book lives under this directory inside
#: the user's cloud folder, whatever prefix the app is configured with.
CACHE_DIR = '.Moon+/Cache'

#: Position markers. Moon+ also writes ``.an`` (annotations and highlights) and
#: whole-library backups next to them; neither carries a reading position.
POSITION_SUFFIX = '.po'


class Marker:
    """One parsed ``.po`` line.

    ``fraction`` is the percentage as the 0..1 number the rest of the codebase
    speaks (`bookshelf.percent`, :func:`sopds_sync.linking.record_progress`).
    """

    __slots__ = ('device', 'chapter', 'volume', 'offset', 'percent')

    def __init__(self, device, chapter, volume, offset, percent):
        self.device = device
        self.chapter = chapter
        self.volume = volume
        self.offset = offset
        self.percent = percent

    @property
    def fraction(self):
        return max(0.0, min(1.0, self.percent / 100.0))

    def replace(self, chapter=None, offset=None, percent=None):
        """A copy with some coordinates changed, keeping the device id.

        Writing back under the device's own id is deliberate: Moon+ Reader wrote
        that number, and a file it does not recognise as its own is not worth
        the risk when the id costs nothing to preserve.
        """
        return Marker(
            self.device,
            self.chapter if chapter is None else chapter,
            self.volume,
            self.offset if offset is None else offset,
            self.percent if percent is None else percent,
        )

    def __str__(self):
        # One decimal, matching what Moon+ Reader itself writes ("23.1%").
        return '%d*%d@%d#%d:%.1f%%' % (self.device, self.chapter, self.volume,
                                       self.offset, self.percent)

    def __eq__(self, other):
        return isinstance(other, Marker) and str(self) == str(other)

    def __repr__(self):
        return '<Marker %s>' % self


def parse(text):
    """A :class:`Marker` from the contents of a ``.po`` file, or None.

    Returns None rather than raising for anything unrecognised: a ``.po`` we
    cannot read is a file we simply store and serve back untouched, and a future
    Moon+ Reader release adding a field must not be able to break the endpoint
    that is holding the user's data.
    """
    if isinstance(text, bytes):
        try:
            text = text.decode('utf-8')
        except UnicodeDecodeError:
            return None
    if not text:
        return None

    match = _MARKER_RE.match(text)
    if not match:
        return None

    return Marker(
        device=int(match.group('device')),
        chapter=int(match.group('chapter')),
        volume=int(match.group('volume')),
        offset=int(match.group('offset')),
        percent=float(match.group('percent')),
    )


def is_position_file(path):
    """Is this DAV sub-path a Moon+ Reader position marker?

    Matches on the cache directory as well as the suffix so that a ``.po`` a
    user happens to store elsewhere in their DAV area — gettext catalogues carry
    the same extension — is left alone.
    """
    path = (path or '').replace('\\', '/')
    return path.endswith(POSITION_SUFFIX) and CACHE_DIR in path


def book_name(path):
    """The book's file name on the device, from the ``.po`` path.

    ``…/Cache/Город Бездны - Рейнольдс Аластер.fb2.po`` -> the name with the
    ``.po`` stripped, which is the book file Moon+ Reader is tracking.
    """
    path = (path or '').replace('\\', '/')
    base = path.rsplit('/', 1)[-1]
    if base.endswith(POSITION_SUFFIX):
        base = base[:-len(POSITION_SUFFIX)]
    return base
