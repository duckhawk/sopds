#import magic
import os
import zipfile
from xml import sax
from io import BytesIO

from book_tools.format.mimetype import Mimetype

from book_tools.format.util import list_zip_file_infos
from book_tools.format.epub import EPub
from book_tools.format.fb2 import FB2, FB2Zip
from book_tools.format.fb2sax import FB2sax
from book_tools.format.other import Dummy
from book_tools.format.mobi import Mobipocket

from constance import config

FB2_ROOT = 'FictionBook'

class mime_detector:
    @staticmethod
    def fmt(fmt):
        if fmt.lower() == 'xml':
            return Mimetype.XML
        elif fmt.lower() == 'fb2':
            return Mimetype.FB2
        elif fmt.lower() =='epub':
            return Mimetype.EPUB
        elif fmt.lower() =='mobi':
            return Mimetype.MOBI
        elif fmt.lower() == 'zip':
            return Mimetype.ZIP
        elif fmt.lower() =='pdf':
            return Mimetype.PDF
        elif fmt.lower() =='doc' or fmt.lower()=='docx':
            return Mimetype.MSWORD
        elif fmt.lower() =='djvu':
            return Mimetype.DJVU
        elif fmt.lower() =='txt':
            return Mimetype.TEXT
        elif fmt.lower() =='rtf':
            return Mimetype.RTF
        else:
            return Mimetype.OCTET_STREAM

    @staticmethod
    def file(filename):
        (n, e) = os.path.splitext(filename)
        return mime_detector.fmt(e[1:])

def detect_mime(file, original_filename):
    mime = mime_detector.file(original_filename)

    try:
        if mime == Mimetype.XML:
            if FB2_ROOT == __xml_root_tag(file):
                return Mimetype.FB2
        elif mime == Mimetype.ZIP:
            with zipfile.ZipFile(file) as zip_file:
                if not zip_file.testzip():
                    infolist = list_zip_file_infos(zip_file)
                    if len(infolist) == 1:
                        if FB2_ROOT == __xml_root_tag(zip_file.open(infolist[0])):
                            return Mimetype.FB2_ZIP
                    try:
                        with zip_file.open('mimetype') as mimetype_file:
                            if mimetype_file.read(30).decode().rstrip('\n\r') == Mimetype.EPUB:
                                return Mimetype.EPUB
                    except Exception:
                        pass
        elif mime == Mimetype.OCTET_STREAM:
            # Unknown or absent extension: fall back to content sniffing so that
            # misnamed / extension-less books are still imported instead of skipped.
            sniffed = __sniff_content(file)
            if sniffed is not None:
                return sniffed
    except:
        pass

    return mime

def create_bookfile(file, original_filename):
    if isinstance(file, str):
        file = open(file, 'rb')
    file = BytesIO(file.read())
    mimetype = detect_mime(file,original_filename)
    if mimetype == Mimetype.EPUB:
        return EPub(file, original_filename)
    elif mimetype == Mimetype.FB2:
        return FB2sax(file, original_filename) if config.SOPDS_FB2SAX else FB2(file, original_filename)
    elif mimetype == Mimetype.FB2_ZIP:
        return FB2Zip(file, original_filename)
    elif mimetype == Mimetype.MOBI:
        return Mobipocket(file, original_filename)
    elif mimetype in [Mimetype.TEXT, Mimetype.PDF, Mimetype.MSWORD, Mimetype.RTF, Mimetype.DJVU]:
       return Dummy(file, original_filename, mimetype)
    else:
        raise Exception('File type \'%s\' is not supported, sorry' % mimetype)

def __sniff_content(file):
    """Detect a book's MIME type from its content (magic bytes / container
    inspection) when the filename extension is unknown or misleading.

    Returns a Mimetype value, or None if nothing recognisable is found. The
    stream position is always restored to the start, since the downstream
    parser re-reads the file from the beginning.
    """
    try:
        file.seek(0)
        head = file.read(80)
    except Exception:
        return None
    finally:
        try:
            file.seek(0)
        except Exception:
            pass

    if head[60:68] == b'BOOKMOBI':
        return Mimetype.MOBI
    if head[:4] == b'%PDF':
        return Mimetype.PDF
    if head[:4] == b'AT&T':          # DjVu (IFF: "AT&T" "FORM" ... "DJVU"/"DJVM")
        return Mimetype.DJVU
    if head[:4] == b'PK\x03\x04':    # ZIP container: EPUB or zipped FB2
        try:
            file.seek(0)
            with zipfile.ZipFile(file) as zip_file:
                if not zip_file.testzip():
                    infolist = list_zip_file_infos(zip_file)
                    if len(infolist) == 1:
                        if FB2_ROOT == __xml_root_tag(zip_file.open(infolist[0])):
                            return Mimetype.FB2_ZIP
                    try:
                        with zip_file.open('mimetype') as mimetype_file:
                            if mimetype_file.read(30).decode().rstrip('\n\r') == Mimetype.EPUB:
                                return Mimetype.EPUB
                    except Exception:
                        pass
            return Mimetype.ZIP
        except Exception:
            return None
        finally:
            try:
                file.seek(0)
            except Exception:
                pass
    if head.lstrip()[:1] == b'<':    # plain XML: maybe a bare FB2
        try:
            file.seek(0)
            if FB2_ROOT == __xml_root_tag(file):
                return Mimetype.FB2
        except Exception:
            pass
        finally:
            try:
                file.seek(0)
            except Exception:
                pass
    return None

def __xml_root_tag(file):
    class XMLRootFound(Exception):
        def __init__(self, name):
            self.name = name

    class RootTagFinder(sax.handler.ContentHandler):
        def startElement(self, name, attributes):
            raise XMLRootFound(name)

    # sax closes the stream it is given (expat's _close_source), so parse a copy
    # to leave the caller's file open for the downstream parser.
    data = file.read()
    try:
        sax.parse(BytesIO(data), RootTagFinder())
    except XMLRootFound as e:
        return e.name
    return None
