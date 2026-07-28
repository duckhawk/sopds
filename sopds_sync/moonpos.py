# -*- coding: utf-8 -*-
"""Moon+ Reader's reading coordinates, computed against our copy of a book.

A Moon+ Reader marker points at a place with a chapter number and a count of
characters scrolled past inside that chapter (see :mod:`sopds_sync.moonreader`).
Both are relative to how Moon+ Reader itself divides the file into chapters, and
nothing in the format says how. Two unknowns follow:

* **Which sections become chapters.** The format documents "an entry in the
  table of contents", which for FB2 could reasonably mean every ``<section>``,
  only the ones carrying a ``<title>``, only the top-level ones, and any of
  those with or without a leading entry for the untitled matter a book opens
  with. The observed markers fit the titled-sections-plus-a-leading-entry
  reading, but one book is not enough to pick a rule and hard-code it.

* **Whether the phone's file is our file.** The name in the cache directory is
  whatever the book is called on the device. A user who fetched a book from
  somewhere other than this catalogue has a different edition, with different
  chapter boundaries, and coordinates computed here would point somewhere else
  entirely.

Both are settled the same way, and by the device rather than by guesswork: an
incoming marker states a percentage *and* the coordinates that produced it, so
we can replay every candidate rule against our copy and keep the one that
reproduces the percentage. A rule that fits is evidence for both — the division
into chapters matches, and so does the text it was measured over. When none
fits, we hold the copy the phone does not have, and writing coordinates back
would move the reader somewhere they never were; the caller is expected to leave
the file alone.

Only FB2 is modelled. It is what the in-browser reader renders through
`FB2_22_xhtml.xsl`, so its paragraph ids can be turned into character offsets;
for any other format :func:`outline` returns None and sync stays one-way.
"""
import logging

from django.core.cache import cache

from opds_catalog import dl

logger = logging.getLogger(__name__)

FB2_NS = '{http://www.gribuser.ru/xml/fictionbook/2.0}'

#: How far a replayed percentage may sit from the one the device reported and
#: still count as the same book. Moon+ Reader rounds to a tenth, and its idea of
#: what counts as a character will not match ours down to the last space, so the
#: window has to be wider than the rounding alone. It stays far below the gap
#: between neighbouring chapters of a real book, which is what it has to
#: distinguish: the sample markers land within 0.05 of their prediction, while
#: chapters sit percent apart.
PERCENT_TOLERANCE = 0.6

#: Candidate readings of "entry in the table of contents", most specific first.
#: `titled` restricts to sections carrying a <title>; `top_level` to sections
#: that are direct children of a <body>; `lead_entry` prepends one entry at the
#: start of the book for the matter before the first chapter.
TOC_RULES = (
    ('titled+lead', {'titled': True, 'top_level': False, 'lead_entry': True}),
    ('titled', {'titled': True, 'top_level': False, 'lead_entry': False}),
    ('all', {'titled': False, 'top_level': False, 'lead_entry': False}),
    ('top+lead', {'titled': True, 'top_level': True, 'lead_entry': True}),
    ('top', {'titled': False, 'top_level': True, 'lead_entry': False}),
)


class Outline:
    """A book's text laid out the way a Moon+ Reader marker measures it.

    `starts` holds the character offset at which each chapter begins under one
    of :data:`TOC_RULES`; `total` is the length of the whole text. `paragraphs`
    maps the in-browser reader's paragraph ids ("2.13", as built by
    `FB2_22_xhtml.xsl`) to the same character offsets, which is what lets a
    position picked in the browser be expressed as a Moon+ Reader coordinate.
    """

    __slots__ = ('rule', 'starts', 'total', 'paragraphs')

    def __init__(self, rule, starts, total, paragraphs):
        self.rule = rule
        self.starts = starts
        self.total = total
        self.paragraphs = paragraphs

    def percent_at(self, chapter, offset):
        """The percentage a marker with these coordinates would report."""
        if not self.total or not (0 <= chapter < len(self.starts)):
            return None
        return 100.0 * (self.starts[chapter] + offset) / self.total

    def paragraph_at(self, char_offset, not_before=None):
        """The browser reader's paragraph id at a character offset, or None.

        The last paragraph that starts at or before the offset: Moon+ Reader
        measures where the text has scrolled off the top of the screen, which
        lands mid-paragraph far more often than not, and the reader has to
        resume at the paragraph being read rather than at the one after it.

        `not_before` bounds the answer from below. A chapter's character count
        starts before its heading, and a heading is not a paragraph, so an
        offset at the very top of a chapter sits in the gap ahead of that
        chapter's first paragraph — and the last paragraph starting before it is
        the closing one of the chapter before. Bounding by the chapter start
        turns that into the chapter's own opening paragraph.
        """
        best_id, best_start = None, -1
        for pid, start in self.paragraphs.items():
            if best_start < start <= char_offset:
                best_id, best_start = pid, start

        if not_before is not None and (best_id is None or best_start < not_before):
            best_id, best_start = None, None
            for pid, start in self.paragraphs.items():
                if start >= not_before and (best_start is None or start < best_start):
                    best_id, best_start = pid, start
        return best_id

    def resume_paragraph(self, chapter, offset):
        """Where the browser reader should open for a device's coordinates."""
        if not 0 <= chapter < len(self.starts):
            return None
        start = self.starts[chapter]
        return self.paragraph_at(start + offset, not_before=start)

    def char_at(self, paragraph_id):
        """The character offset a browser-reader paragraph id sits at, or None."""
        return self.paragraphs.get(paragraph_id)

    def locate(self, char_offset):
        """``(chapter, offset within chapter, percent)`` for a character offset."""
        chapter = 0
        for i, start in enumerate(self.starts):
            if start > char_offset:
                break
            chapter = i
        within = max(0, char_offset - self.starts[chapter])
        percent = 100.0 * char_offset / self.total if self.total else 0.0
        return chapter, within, percent


def _text_len(element):
    """Characters of text inside an element, counted as the outline counts them."""
    return len(''.join(element.itertext()))


def _walk(book_root):
    """Every section and paragraph of an FB2 body, in reading order.

    Yields ``('section', element, depth, start)`` and ``('p', paragraph_id,
    start)`` as the character cursor advances, so a single pass produces both
    the chapter boundaries and the paragraph offsets. Bodies after the first
    hold footnotes; they are part of the text Moon+ Reader pages through, so
    they are walked too.
    """
    cursor = 0

    def descend(element, depth, section_index_path):
        nonlocal cursor
        # Both counters mirror what the XSL asks lxml for per element —
        # `preceding-sibling::fb:section` and `xsl:number` — but kept as running
        # totals. Asking per element walks the siblings again every time, which
        # is quadratic in a chapter's paragraph count and turns parsing a novel
        # into tens of seconds.
        sections_before = 0
        paragraphs_before = 0
        for child in element:
            tag = child.tag
            if tag == FB2_NS + 'section':
                # The id the XSL builds counts preceding siblings across every
                # ancestor section, so the running total is carried down.
                index = section_index_path + sections_before
                yield 'section', child, depth, cursor, index
                yield from descend(child, depth + 1, index)
                sections_before += 1
            elif tag == FB2_NS + 'p':
                # xsl:number counts p elements among their siblings, 1-based.
                paragraphs_before += 1
                yield 'p', '%d.%d' % (section_index_path + 1, paragraphs_before), cursor
                cursor += _text_len(child)
            else:
                # Titles, epigraphs, poems: text that is read but is not a
                # paragraph the browser reader can be positioned on.
                cursor += _text_len(child)

    for body in book_root.findall(FB2_NS + 'body'):
        yield from descend(body, 0, 0)


def _build(book_root, rule_name, rule):
    starts, paragraphs = [], {}
    for item in _walk(book_root):
        if item[0] == 'section':
            _, element, depth, start, _index = item
            titled = element.find(FB2_NS + 'title') is not None
            if rule['titled'] and not titled:
                continue
            if rule['top_level'] and depth > 0:
                continue
            starts.append(start)
        else:
            _, pid, start = item
            # First paragraph wins: the XSL can mint the same id twice in a
            # deeply nested book, and the earlier one is the one a reader
            # scrolling from the top reaches.
            paragraphs.setdefault(pid, start)

    total = sum(_text_len(b) for b in book_root.findall(FB2_NS + 'body'))
    if rule['lead_entry'] and (not starts or starts[0] != 0):
        starts = [0] + starts

    return Outline(rule_name, starts, total, paragraphs)


def _parse(book):
    """The book's FB2 root element, or None if we cannot read or parse it."""
    if (book.format or '').lower() != 'fb2':
        return None
    try:
        from lxml import etree
        data = dl.getFileData(book)
        if data is None:
            return None
        return etree.parse(data).getroot()
    except Exception:
        logger.exception('Could not parse FB2 for book %s', book.id)
        return None


def candidates(book):
    """An :class:`Outline` per candidate rule, or None if the book is unusable."""
    root = _parse(book)
    if root is None:
        return None
    built = []
    for name, rule in TOC_RULES:
        try:
            built.append(_build(root, name, rule))
        except Exception:
            logger.exception('Could not build outline %s for book %s', name, book.id)
    return built or None


def _cache_key(book, rule_name):
    # Keyed on the size as well as the id, so a book replaced on disk by another
    # edition is measured afresh rather than against the outline of the old one.
    return 'moonpos:%s:%s:%s' % (book.id, book.filesize, rule_name)


def for_rule(book, rule_name):
    """The outline under one named rule, or None if it cannot be built.

    Cached: this runs whenever a position is saved in the browser reader, and
    building it parses the whole book — a couple of megabytes of XML for a
    novel — which is far too much to repeat on every scroll.
    """
    key = _cache_key(book, rule_name)
    outline = cache.get(key)
    if outline is not None:
        return outline

    rule = dict(TOC_RULES).get(rule_name)
    if rule is None:
        return None
    root = _parse(book)
    if root is None:
        return None
    try:
        outline = _build(root, rule_name, rule)
    except Exception:
        logger.exception('Could not build outline %s for book %s', rule_name, book.id)
        return None

    cache.set(key, outline, 24 * 3600)
    return outline


def fit(book, marker):
    """``(outline, gap)`` for the rule whose replay of `marker` reproduces its
    percentage, or ``(None, None)``.

    This is the check that decides whether we may write coordinates back: a hit
    means our copy is divided into chapters the same way and measures the same
    length, so a coordinate computed here means on the phone what it means here.

    The gap comes back with it because one book is rarely the only candidate: a
    catalogue can hold several editions of a novel, all of them close enough to
    pass, and how closely each reproduced the marker is the only thing left to
    tell them apart.
    """
    built = candidates(book)
    if not built:
        return None, None

    best, best_gap = None, None
    for outline in built:
        predicted = outline.percent_at(marker.chapter, marker.offset)
        if predicted is None:
            continue
        gap = abs(predicted - marker.percent)
        if gap <= PERCENT_TOLERANCE and (best_gap is None or gap < best_gap):
            best, best_gap = outline, gap

    if best is not None:
        logger.debug('Book %s matches Moon+ rule %s (%.2f%% off)',
                     book.id, best.rule, best_gap)
        cache.set(_cache_key(book, best.rule), best, 24 * 3600)
    return best, best_gap
