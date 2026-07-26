"""Rendering an EPUB into the fragment the in-browser reader consumes.

An EPUB is markup written by somebody else, so most of this is about what does
*not* survive the trip.
"""
import io
import os
import re
import zipfile

import pytest

from opds_catalog import epub_render

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
REAL_EPUB = os.path.join(DATA, 'mirer.epub')

CONTAINER = '''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>'''

OPF = '''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <manifest>%s</manifest>
  <spine>%s</spine>
</package>'''


def build_epub(documents, extra=None):
    """A minimal EPUB from {href: xhtml}, in the order given."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', CONTAINER)
        items, refs = [], []
        for n, (href, body) in enumerate(documents.items(), start=1):
            items.append('<item id="d%d" href="%s" media-type="application/xhtml+xml"/>' % (n, href))
            refs.append('<itemref idref="d%d"/>' % n)
            archive.writestr('OEBPS/' + href,
                             '<?xml version="1.0" encoding="utf-8"?>'
                             '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                             '<title>t</title></head><body>%s</body></html>' % body)
        archive.writestr('OEBPS/content.opf', OPF % (''.join(items), ''.join(refs)))
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    buffer.seek(0)
    return buffer


def render(documents, extra=None, resource=lambda p: '/res/' + p):
    return epub_render.render(build_epub(documents, extra), resource)


# --- structure the reader depends on ---------------------------------------

def test_each_spine_document_becomes_a_chapter_marker():
    html = render({'a.xhtml': '<p>one</p>', 'b.xhtml': '<p>two</p>'})
    assert html.count('<a name="TOC_1">') == 1
    assert html.count('<a name="TOC_2">') == 1


def test_paragraphs_are_divs_numbered_by_document():
    """The reader saves a position as the id of a `div`, "section.paragraph"."""
    html = render({'a.xhtml': '<p>one</p><p>two</p>', 'b.xhtml': '<p>three</p>'})
    assert '<div id="1.1"' in html and '<div id="1.2"' in html
    assert '<div id="2.1"' in html
    assert '<p' not in html


def test_spine_order_is_preserved():
    html = render({'a.xhtml': '<p>first</p>', 'b.xhtml': '<p>second</p>'})
    assert html.index('first') < html.index('second')


def test_text_survives():
    html = render({'a.xhtml': '<p>Hello <em>there</em>, reader</p>'})
    assert 'Hello' in html and 'there' in html and 'reader' in html
    assert '<em>' in html


def test_the_real_sample_book_renders():
    with open(REAL_EPUB, 'rb') as f:
        html = epub_render.render(f, lambda p: '/res/' + p)
    assert len(re.findall(r'<a name="TOC_\d+"></a>', html)) > 1
    assert len(re.findall(r'<div id="\d+\.\d+"', html)) > 100
    assert 'xmlns' not in html          # namespaces stripped, not serialised


# --- untrusted markup ------------------------------------------------------

def test_scripts_are_removed_with_their_contents():
    html = render({'a.xhtml': '<p>before</p><script>alert(1)</script><p>after</p>'})
    assert '<script' not in html and 'alert(1)' not in html
    assert 'before' in html and 'after' in html


@pytest.mark.parametrize('markup', [
    '<iframe src="http://evil/"></iframe>',
    '<object data="x.swf"></object>',
    '<embed src="x">',
    '<form action="http://evil/"><input name="p"></form>',
    '<style>body{display:none}</style>',
    '<link rel="stylesheet" href="http://evil/x.css">',
    '<svg><script>alert(1)</script></svg>',
])
def test_active_and_remote_content_is_removed(markup):
    html = render({'a.xhtml': markup + '<p>kept</p>'})
    # Prose after the offending element survives even when a sloppy, unclosed
    # tag made the fallback parser nest it inside.
    assert 'kept' in html
    for banned in ('iframe', 'object', 'embed', '<form', '<input', '<style',
                   '<link', '<svg', 'alert', 'evil'):
        assert banned not in html, markup


def test_event_handlers_are_stripped():
    html = render({'a.xhtml': '<p onclick="steal()" onerror="x()">text</p>'})
    assert 'onclick' not in html and 'onerror' not in html and 'steal' not in html
    assert 'text' in html


@pytest.mark.parametrize('href', [
    'javascript:alert(1)', 'JavaScript:alert(1)', 'data:text/html,<script>x</script>',
    'file:///etc/passwd', 'vbscript:x',
])
def test_dangerous_link_schemes_do_not_survive(href):
    html = render({'a.xhtml': '<p><a href="%s">click</a></p>' % href})
    assert 'href' not in html
    assert 'click' in html          # the anchor is unwrapped, its text stays


def test_external_links_are_kept_but_defanged():
    html = render({'a.xhtml': '<p><a href="https://example.com/x">site</a></p>'})
    assert 'https://example.com/x' in html
    assert 'nofollow' in html and 'noopener' in html


def test_book_styling_is_not_carried_into_the_reader():
    html = render({'a.xhtml': '<p class="fancy" style="color:red" id="mine">text</p>'})
    assert 'class=' not in html and 'style=' not in html
    assert 'id="mine"' not in html          # would collide with the reader's ids
    assert '<div id="1.1"' in html


# --- images ----------------------------------------------------------------

def test_images_inside_the_archive_are_rewritten_to_the_resource_url():
    html = render({'a.xhtml': '<p><img src="img/pic.png" alt="a"/></p>'},
                  extra={'OEBPS/img/pic.png': b'\x89PNG'})
    assert 'src="/res/OEBPS/img/pic.png"' in html
    assert 'alt="a"' in html


def test_remote_images_are_dropped():
    html = render({'a.xhtml': '<p><img src="http://evil/track.gif"><span>t</span></p>'})
    assert '<img' not in html and 'evil' not in html
    assert 't' in html


def test_images_that_are_not_in_the_archive_are_dropped():
    html = render({'a.xhtml': '<p><img src="missing.png">text</p>'})
    assert '<img' not in html
    assert 'text' in html


def test_a_traversing_image_path_cannot_escape_the_archive():
    html = render({'a.xhtml': '<p><img src="../../../../etc/passwd"></p>'})
    assert '<img' not in html
    assert 'passwd' not in html


# --- malformed input -------------------------------------------------------

def test_a_document_that_is_not_well_formed_xml_still_renders():
    """Real EPUBs are frequently sloppy despite the specification."""
    html = render({'a.xhtml': '<p>unclosed <b>bold</p>'})
    assert 'unclosed' in html and 'bold' in html


def test_an_archive_without_a_container_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('random.txt', 'not a book')
    buffer.seek(0)
    with pytest.raises(epub_render.EpubError):
        epub_render.render(buffer, lambda p: p)


def test_an_empty_spine_is_rejected():
    with pytest.raises(epub_render.EpubError):
        render({})


def test_a_spine_entry_with_no_file_is_skipped():
    buffer = build_epub({'a.xhtml': '<p>kept</p>'})
    with zipfile.ZipFile(buffer, 'a') as archive:
        opf = archive.read('OEBPS/content.opf').decode()
    patched = opf.replace('</manifest>',
                          '<item id="ghost" href="ghost.xhtml" media-type="application/xhtml+xml"/></manifest>')
    patched = patched.replace('</spine>', '<itemref idref="ghost"/></spine>')

    rebuilt = io.BytesIO()
    with zipfile.ZipFile(buffer) as src, zipfile.ZipFile(rebuilt, 'w') as dst:
        for item in src.infolist():
            payload = patched if item.filename == 'OEBPS/content.opf' else src.read(item)
            dst.writestr(item.filename, payload)
    rebuilt.seek(0)

    html = epub_render.render(rebuilt, lambda p: p)
    assert 'kept' in html
