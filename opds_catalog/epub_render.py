# -*- coding: utf-8 -*-
"""Render an EPUB to the flat XHTML fragment the in-browser reader consumes.

The reader was written against the FB2 path, where FB2_22_xhtml.xsl turns a book
into one stream of ``<div id="S.P">`` paragraphs separated by ``<a name="TOC_n">``
chapter markers. Everything the reader does — remembering your position, the
progress bar, chapter mode, the font-size preference — is expressed in terms of
that shape. So rather than bolt a second, client-side reader onto the page for
EPUB, this produces the same shape server-side and the existing reader works on
EPUB unchanged.

An EPUB is a zip of XHTML documents written by someone else, so the markup is
untrusted and is rebuilt rather than filtered: only known-safe elements and
attributes survive (see ALLOWED_*), everything else is either dropped with its
subtree (scripts, styles, embedded objects) or unwrapped to its text. A denylist
would have to anticipate every dangerous construct; an allowlist only has to
know the harmless ones.
"""
import logging
import posixpath
import zipfile
from urllib.parse import unquote, urlparse

import lxml.etree as ET
import lxml.html

from book_tools.format.util import safe_lxml_parser

logger = logging.getLogger(__name__)

CONTAINER = 'META-INF/container.xml'
OPF_NS = 'http://www.idpf.org/2007/opf'
CONTAINER_NS = 'urn:oasis:names:tc:opendocument:xmlns:container'

# Documents we will render from the spine.
TEXT_TYPES = ('application/xhtml+xml', 'text/html', 'application/x-dtbook+xml')

# Dropped along with everything inside them, because what is inside is code or
# metadata rather than prose: keeping the text would splice CSS and JavaScript
# source into the middle of the book.
#
# Deliberately short. Every other unwanted element — iframe, object, embed,
# form controls, svg, media — is merely unwrapped, so its descendants are still
# judged individually by the allowlist. That matters because the HTML fallback
# parser nests whatever follows an unclosed tag *inside* it: with <embed> in the
# drop-subtree list, one stray unclosed tag in a sloppy EPUB silently swallowed
# the rest of the chapter.
DROP_SUBTREE = frozenset((
    'script', 'style', 'link', 'meta', 'base', 'title', 'head',
))

# Kept as elements. Anything not here and not in DROP_SUBTREE is unwrapped: the
# tag goes, its text and children stay.
ALLOWED_TAGS = frozenset((
    'div', 'p', 'br', 'hr', 'span',
    'em', 'i', 'strong', 'b', 'u', 's', 'strike', 'del', 'ins', 'small',
    'sub', 'sup', 'abbr', 'cite', 'q', 'code', 'kbd', 'samp', 'var', 'pre',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'caption',
    'img', 'a', 'figure', 'figcaption', 'ruby', 'rt', 'rp',
))

# Attributes kept per tag. `class`, `style` and `id` are deliberately absent:
# the book's own styling is not wanted inside our reader chrome, and an incoming
# `id` could collide with the "S.P" ids the reader navigates by.
ALLOWED_ATTRS = {
    None: frozenset(('title', 'lang', 'dir')),
    'a': frozenset(('href',)),
    'img': frozenset(('src', 'alt', 'width', 'height')),
    'td': frozenset(('colspan', 'rowspan')),
    'th': frozenset(('colspan', 'rowspan')),
    'ol': frozenset(('start',)),
}

# Only these survive on a link. A book may legitimately point at the web; it may
# not point at javascript:, data: or file:.
SAFE_SCHEMES = frozenset(('http', 'https', 'mailto'))

# Guard against a book with an absurd number of spine documents.
MAX_DOCUMENTS = 2000


class EpubError(Exception):
    """The file is not an EPUB we can render."""


def _parse_xml(data):
    """Parse a document from the archive with entity resolution disabled.

    Real EPUBs are frequently not well-formed XML despite the specification, so
    fall back to the HTML parser. Both are configured not to fetch anything.
    """
    try:
        return ET.fromstring(data, parser=safe_lxml_parser())
    except ET.XMLSyntaxError:
        parser = lxml.html.HTMLParser(no_network=True, recover=True)
        return lxml.html.fromstring(data, parser=parser)


def _localname(tag):
    if not isinstance(tag, str):
        return ''
    return tag.rsplit('}', 1)[-1].lower()


def opf_path(archive):
    """Path of the package document, per META-INF/container.xml."""
    try:
        container = _parse_xml(archive.read(CONTAINER))
    except KeyError:
        raise EpubError('no %s' % CONTAINER)

    for element in container.iter():
        if _localname(element.tag) == 'rootfile':
            path = element.get('full-path')
            if path:
                return path
    raise EpubError('no rootfile in %s' % CONTAINER)


def spine_documents(archive):
    """The archive paths of the reading-order documents, in order."""
    package_path = opf_path(archive)
    base = posixpath.dirname(package_path)

    try:
        package = _parse_xml(archive.read(package_path))
    except KeyError:
        raise EpubError('package document %s is missing' % package_path)

    manifest, spine = {}, []
    for element in package.iter():
        name = _localname(element.tag)
        if name == 'item':
            item_id, href = element.get('id'), element.get('href')
            if item_id and href:
                manifest[item_id] = (href, (element.get('media-type') or '').lower())
        elif name == 'itemref':
            idref = element.get('idref')
            if idref:
                spine.append(idref)

    documents = []
    for idref in spine[:MAX_DOCUMENTS]:
        entry = manifest.get(idref)
        if entry is None:
            continue
        href, media_type = entry
        if media_type and media_type not in TEXT_TYPES:
            continue
        documents.append(resolve(base, href))

    if not documents:
        raise EpubError('no readable documents in the spine')

    return documents


def resolve(base, href):
    """An archive path from a base directory and a possibly-relative href."""
    href = unquote((href or '').split('#', 1)[0])
    return posixpath.normpath(posixpath.join(base, href)).lstrip('/')


def _safe_href(value):
    """A link the reader may keep, or None.

    Intra-book links are dropped: their targets live in ids we strip, so they
    would be dead anyway, and the anchor is unwrapped so the footnote marker or
    caption text survives as plain text.
    """
    value = (value or '').strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme.lower() in SAFE_SCHEMES and parsed.netloc:
        return value
    if parsed.scheme.lower() == 'mailto' and parsed.path:
        return value
    return None


def sanitize(element, resource):
    """Rebuild a parsed document body in place, keeping only what is allowed.

    `resource(path) -> url or None` maps an image reference inside the archive
    to a URL the browser may fetch; returning None drops the image.
    """
    for node in list(element.iter()):
        # The root is the caller's <body>; it is retagged to a <div> afterwards
        # and must not be judged against the allowlist, which would unwrap it
        # and leave the caller holding an empty element.
        if node is element:
            continue

        name = _localname(node.tag)

        if not isinstance(node.tag, str):     # comment / processing instruction
            _drop(node, keep_children=False)
            continue

        if name in DROP_SUBTREE:
            _drop(node, keep_children=False)
            continue

        if name not in ALLOWED_TAGS:
            _drop(node, keep_children=True)
            continue

        allowed = ALLOWED_ATTRS.get(name, frozenset()) | ALLOWED_ATTRS[None]
        for attribute in list(node.attrib):
            # Namespaced and on* attributes never survive; the rest must be
            # named in the allowlist for this tag.
            if attribute.lower() not in allowed or attribute.startswith('{'):
                del node.attrib[attribute]

        if name == 'a':
            href = _safe_href(node.get('href'))
            if href is None:
                _drop(node, keep_children=True)
            else:
                node.set('href', href)
                node.set('rel', 'nofollow noopener noreferrer')
                node.set('target', '_blank')

        elif name == 'img':
            url = resource(node.get('src'))
            if url is None:
                _drop(node, keep_children=False)
            else:
                node.set('src', url)
                node.set('loading', 'lazy')


def _append_text(parent, previous, text):
    """Add loose text to a parent, after `previous` if there is one."""
    if not text:
        return
    if previous is not None:
        previous.tail = (previous.tail or '') + text
    else:
        parent.text = (parent.text or '') + text


def _drop(node, keep_children):
    """Remove a node, optionally splicing its text and children into its parent.

    `lxml.html` has `drop_tag()` for this, but a well-formed EPUB parses as XML
    and yields plain `_Element`s, which do not have it — so the splice is done
    by hand and works for whichever parser handled the document.
    """
    parent = node.getparent()
    if parent is None:
        return

    previous = node.getprevious()

    if not keep_children:
        _append_text(parent, previous, node.tail)
        parent.remove(node)
        return

    _append_text(parent, previous, node.text)

    index = parent.index(node)
    children = list(node)
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)

    # The tail belongs after whatever now stands in the node's place.
    _append_text(parent, children[-1] if children else previous, node.tail)
    parent.remove(node)


def strip_namespaces(root):
    """Reduce every tag to its local name.

    XHTML parsed as XML comes back as `{http://www.w3.org/1999/xhtml}p`. Left
    alone, that survives into the serialised fragment as prefixed tag names the
    browser would not recognise.
    """
    for node in root.iter():
        if isinstance(node.tag, str) and '}' in node.tag:
            node.tag = node.tag.rsplit('}', 1)[-1]
    ET.cleanup_namespaces(root)


def number_paragraphs(body, index):
    """Give each paragraph the `<div id="section.paragraph">` the reader tracks.

    The reader saves and restores a position by the id of a `div`, so a `<p>`
    becomes a `<div>` — which is also what the FB2 stylesheet emits.
    """
    counter = 0
    for node in body.iter():
        if _localname(node.tag) == 'p':
            counter += 1
            node.tag = 'div'
            node.set('id', '%d.%d' % (index, counter))
    return counter


def render(fileobj, resource_url):
    """The whole book as one XHTML fragment, from a file-like object."""
    with zipfile.ZipFile(fileobj) as archive:
        return render_archive(archive, resource_url)


def render_archive(archive, resource_url):
    """The whole book as one XHTML fragment, from an open archive.

    Taking the archive rather than a path lets a caller that already has one
    open reuse it — the reader serves the text and every illustration out of the
    same file, and reopening it per request is what made that expensive.

    `resource_url(archive_path) -> url` builds the URL for an image; it may
    return None to drop one.
    """
    documents = spine_documents(archive)
    names = set(archive.namelist())
    parts = []

    for index, path in enumerate(documents, start=1):
        if path not in names:
            logger.debug('EPUB spine references a missing document: %s', path)
            continue
        try:
            document = _parse_xml(archive.read(path))
        except (ET.XMLSyntaxError, ET.ParserError, ValueError) as err:
            logger.debug('EPUB document %s did not parse: %s', path, err)
            continue

        # Namespaces come off the whole document, not just the body: a
        # declaration is only dropped once nothing under its owner uses it,
        # and the xhtml default namespace is declared on <html>.
        strip_namespaces(document)
        body = next((e for e in document.iter()
                     if _localname(e.tag) == 'body'), document)

        base = posixpath.dirname(path)
        sanitize(body, lambda src, base=base: _image(src, base, names, resource_url))
        number_paragraphs(body, index)

        # The chapter marker the reader splits on, then the document itself
        # flattened to a div so the fragment stays one flat stream.
        parts.append('<a name="TOC_%d"></a>' % index)
        body.tag = 'div'
        for attribute in list(body.attrib):
            del body.attrib[attribute]
        parts.append(ET.tostring(body, encoding='unicode', method='html'))

    if not parts:
        raise EpubError('nothing renderable in the archive')

    return ''.join(parts)


def _image(src, base, names, resource_url):
    """Map an <img src> inside the archive to a URL, or None to drop it."""
    if not src:
        return None
    if urlparse(src).scheme:      # remote or javascript:/data: — never followed
        return None

    path = resolve(base, src)
    if path not in names:
        return None
    return resource_url(path)
