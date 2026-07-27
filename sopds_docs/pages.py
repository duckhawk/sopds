# -*- coding: utf-8 -*-
"""The documentation under /docs: markdown files, discovered and rendered.

Written as files rather than templates because documentation is prose that
changes far more often than code, and prose is easier to write, review and
translate as markdown than as HTML wrapped in template tags. There is no build
step in this project, so the rendering happens at request time and is memoised
against the file's mtime — in a deployed image the files never change, so each
page is rendered once per worker and then served from memory.

Filenames carry a two-digit ordering prefix that is not part of the URL:
`10-getting-started.md` is served as `/docs/getting-started/`. Reordering the
section is renaming a file, not editing a list somewhere else that would drift
out of step with what is actually there.

The content is ours and ships inside the image, so it is rendered as trusted
markdown. Nothing a reader can write ever reaches this module.
"""
import functools
import logging
import os
import re

from django.utils.translation import get_language

logger = logging.getLogger(__name__)

CONTENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'content')

# English is the source language, the one the interface strings are written in,
# so it is also what a language without its own translation of a page falls back
# to — a page in the wrong language beats a 404.
DEFAULT_LANGUAGE = 'en'

FILENAME = re.compile(r'^(\d+)-([a-z0-9-]+)\.md$')
HEADING = re.compile(r'^#\s+(.+?)\s*$', re.MULTILINE)

MARKDOWN_EXTENSIONS = ['extra', 'sane_lists', 'toc']


def language():
    """Which set of pages to serve: the interface language, or the fallback."""
    code = (get_language() or DEFAULT_LANGUAGE).split('-')[0]
    return code if os.path.isdir(os.path.join(CONTENT, code)) else DEFAULT_LANGUAGE


def _stamp(directory):
    """A value that changes whenever the files in `directory` do.

    The memoised readers below are keyed on it. In a deployed image it never
    changes; in a checkout it means an edited page shows up on reload.
    """
    try:
        with os.scandir(directory) as it:
            return tuple(sorted((e.name, e.stat().st_mtime_ns) for e in it if e.is_file()))
    except OSError:
        return ()


@functools.lru_cache(maxsize=8)
def _index(lang, _stamped):
    """Every page of this language, in file order: [(slug, title), ...]."""
    directory = os.path.join(CONTENT, lang)
    pages = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return ()

    for name in names:
        match = FILENAME.match(name)
        if match is None:
            continue
        slug = match.group(2)
        pages.append((slug, _title(os.path.join(directory, name), slug)))
    return tuple(pages)


def _title(path, slug):
    """The page's first level-one heading, or its slug if it has none."""
    try:
        with open(path, encoding='utf-8') as f:
            found = HEADING.search(f.read(4096))
    except OSError:
        return slug
    return found.group(1) if found else slug


def index(lang=None):
    lang = lang or language()
    return _index(lang, _stamp(os.path.join(CONTENT, lang)))


def _path(lang, slug):
    directory = os.path.join(CONTENT, lang)
    try:
        names = os.listdir(directory)
    except OSError:
        return None

    for name in names:
        match = FILENAME.match(name)
        if match is not None and match.group(2) == slug:
            return os.path.join(directory, name)
    return None


@functools.lru_cache(maxsize=64)
def _render(path, _mtime):
    import markdown

    with open(path, encoding='utf-8') as f:
        text = f.read()
    return markdown.markdown(text, extensions=MARKDOWN_EXTENSIONS, output_format='html')


def render(slug, lang=None):
    """The page as HTML, plus the language it was actually found in.

    Returns (html, lang) or (None, None). A page missing from a translation
    falls back to the source language rather than disappearing, so adding an
    English section does not have to wait for its translation to exist.
    """
    wanted = lang or language()
    for candidate in (wanted, DEFAULT_LANGUAGE):
        path = _path(candidate, slug)
        if path is None:
            continue
        try:
            return _render(path, os.stat(path).st_mtime_ns), candidate
        except OSError as err:
            logger.warning('Cannot read documentation page %s: %s', path, err)
            return None, None
    return None, None


def first_slug(lang=None):
    """The page /docs/ itself shows. None when there is no documentation."""
    pages = index(lang)
    return pages[0][0] if pages else None
