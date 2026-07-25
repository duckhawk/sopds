#import PythonMagick
#from PIL import Image, ImageFile
import re

from lxml import etree

strip_symbols = " »«'\"&\n-.#\\`"


def _valid_isbn10(s):
    """True if `s` is a checksum-valid 10-char ISBN (last char may be 'X')."""
    if len(s) != 10:
        return False
    total = 0
    for i, ch in enumerate(s):
        if ch == 'X' and i == 9:
            v = 10
        elif ch.isdigit():
            v = int(ch)
        else:
            return False
        total += (10 - i) * v
    return total % 11 == 0


def _valid_isbn13(s):
    """True if `s` is a checksum-valid 13-digit ISBN."""
    if len(s) != 13 or not s.isdigit():
        return False
    total = sum((1 if i % 2 == 0 else 3) * int(ch) for i, ch in enumerate(s))
    return total % 10 == 0


def normalize_isbn(raw):
    """Normalise a raw metadata ISBN to bare digits (ISBN-13 / ISBN-10).

    Accepts values as they appear in FB2/EPUB metadata: hyphenated or spaced,
    with an ``ISBN``/``urn:isbn:`` prefix, or several identifiers packed in one
    field (the first checksum-valid one wins). Returns '' when nothing valid is
    found, so a junk or invalid identifier is simply not stored.
    """
    if not raw or not isinstance(raw, str):
        return ''
    # Split only on list separators (a field may hold several identifiers);
    # spaces are group separators inside a single ISBN and are stripped below.
    for token in re.split(r'[;,]+', raw.strip()):
        t = token.strip().lower()
        for pref in ('urn:isbn:', 'isbn:', 'isbn'):
            if t.startswith(pref):
                t = t[len(pref):]
                break
        t = t.replace('-', '').replace(' ', '').upper()
        if _valid_isbn13(t) or _valid_isbn10(t):
            return t
    return ''


def safe_lxml_parser():
    """A hardened, per-call lxml parser for untrusted book XML.

    Book files are attacker-controlled; the default lxml parser resolves
    entities and can be driven to disclose local files (`file://` XXE) or
    exhaust memory (billion-laughs). Disable entity resolution, DTD loading,
    network access and the huge-tree allowance. A fresh parser is returned per
    call because lxml parsers are not safe to share across threads.
    See the OWASP XXE Prevention Cheat Sheet and defusedxml (lxml section).
    """
    return etree.XMLParser(resolve_entities=False, no_network=True,
                           load_dtd=False, huge_tree=False, dtd_validation=False)


def harden_expat(parser):
    """Block entity expansion on an expat parser (mirrors defusedxml).

    Internal entity declarations are the billion-laughs vector and external
    entity references are the XXE vector; refuse both so a crafted FB2 is
    rejected (caught by the parser's broad except -> counted as a bad book)
    rather than expanded.
    """
    def _forbid_entity(*args):
        raise ValueError('XML entity declarations are not allowed')

    def _forbid_external(*args):
        raise ValueError('External XML entities are not allowed')

    parser.EntityDeclHandler = _forbid_entity
    parser.ExternalEntityRefHandler = _forbid_external
    return parser


def list_zip_file_infos(zipfile):
    return [info for info in zipfile.infolist() if not info.filename.endswith('/')]

def minify_cover(path):
    # try:
    #     try:
    #         image = Image.open(path).convert('RGB')
    #     except:
    #         magick_image = PythonMagick.Image(path + '[0]')
    #         magick_image.write(path)
    #         image = Image.open(path).convert('RGB')
    #     width = image.size[0]
    #     if width > 600:
    #         new_width = 500
    #         new_height = int(float(new_width) * image.size[1] / width)
    #         image.thumbnail((new_width, new_height), Image.ANTIALIAS)
    #     ImageFile.MAXBLOCK = image.size[0] * image.size[1]
    #     image.save(path, 'JPEG', optimize=True, progressive=True)
    # except:
    #     pass
    pass
