#import PythonMagick
#from PIL import Image, ImageFile
from lxml import etree

strip_symbols = " »«'\"&\n-.#\\`"


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
