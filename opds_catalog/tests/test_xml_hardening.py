"""XXE / billion-laughs hardening of the book XML parsers (#35).

Book files are untrusted; the parsers must not resolve external entities
(file:// disclosure) or expand internal entities (billion-laughs).
"""
from io import BytesIO

from lxml import etree

from book_tools.format.util import safe_lxml_parser
from book_tools.format import fb2sax
from opds_catalog import fb2parse

BILLION_LAUGHS = (
    b'<?xml version="1.0"?>\n'
    b'<!DOCTYPE FictionBook [\n'
    b'  <!ENTITY a "AAAAAAAAAA">\n'
    b'  <!ENTITY b "&a;&a;&a;&a;&a;">\n'
    b']>\n'
    b'<FictionBook><description><title-info>'
    b'<book-title>&b;</book-title></title-info></description></FictionBook>'
)


def _xxe_doc(path):
    return (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE FictionBook [<!ENTITY xxe SYSTEM "file://%s">]>\n'
        '<FictionBook><description><title-info>'
        '<book-title>&xxe;</book-title></title-info></description></FictionBook>'
        % path
    ).encode()


def test_fb2parse_expat_rejects_entities():
    p = fb2parse.fb2parser()
    p.parse(BytesIO(BILLION_LAUGHS))
    assert p.parse_error == 1


def test_fb2sax_expat_rejects_entities():
    p = fb2sax.fb2parser()
    p.parse(BytesIO(BILLION_LAUGHS))
    assert p.parse_error == 1


def test_safe_lxml_parser_does_not_expand_entities():
    try:
        root = etree.fromstring(BILLION_LAUGHS, parser=safe_lxml_parser())
        text = ''.join(root.itertext())
    except etree.XMLSyntaxError:
        text = ''
    assert 'AAAA' not in text


def test_safe_lxml_parser_blocks_xxe_file_read(tmp_path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('TOPSECRET')
    try:
        root = etree.fromstring(_xxe_doc(secret), parser=safe_lxml_parser())
        text = ''.join(root.itertext())
    except etree.XMLSyntaxError:
        text = ''
    assert 'TOPSECRET' not in text
