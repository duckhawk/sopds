# -*- coding: utf-8 -*-
import os
import codecs
import hashlib
import io
import shlex
import subprocess
import contextlib
import lxml.etree as ET
import mimetypes
import posixpath
from functools import wraps
from re import search
import logging

from django.core.cache import cache
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.views.decorators.http import etag

from opds_catalog.models import Book, bookshelf
from opds_catalog import settings, utils, opdsdb, epub_render, paged, stats, throttle
import zipfile
from opds_catalog.ziptools import open_zipfile

from book_tools.format import create_bookfile, mime_detector
from book_tools.format.mimetype import Mimetype
from book_tools.format.util import safe_lxml_parser

from constance import config
from PIL import Image

from opds_catalog.middleware import BasicAuthMiddleware


logger = logging.getLogger(__name__)

# Cover images come from untrusted books. Cap the pixel budget so a crafted
# tiny-but-huge-dimension image raises DecompressionBombError instead of
# allocating gigabytes (real covers are far below this).
Image.MAX_IMAGE_PIXELS = 64_000_000

# Upper bound on a single illustration served out of an EPUB. Real cover art and
# plates are far below this; the cap stops a crafted archive from making the
# reader page pull a multi-gigabyte member.
MAX_RESOURCE_BYTES = 32 * 1024 * 1024

# Illustrations at or below this go into the shared cache; anything larger is
# streamed straight from the archive rather than evicting everything else.
MAX_CACHED_RESOURCE_BYTES = 1024 * 1024

# A rendered book is a few hundred kilobytes of HTML, which is worth caching;
# an enormous one is not worth pushing everything else out of the cache for.
MAX_CACHED_RENDER_BYTES = 4 * 1024 * 1024


def authenticate_catalog(request):
    """Resolve the caller against SOPDS_AUTH, or return a 401 to send back.

    Returns None when the request may proceed. Shared by the content routes and
    by the OPDS 2.0 feeds, which authenticate the same way but are not throttled
    — listing is cheap, handing out content is what costs.
    """
    if config.SOPDS_AUTH and not request.user.is_authenticated:
        # Returns the request (with .user set) on success, a 401 otherwise.
        result = BasicAuthMiddleware().process_request(request)
        if result is not None and not hasattr(result, 'user'):
            return result
    return None


def require_login(view):
    """Authenticate, without the rate limit. For browsing, not for content."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        refused = authenticate_catalog(request)
        if refused is not None:
            return refused
        return view(request, *args, **kwargs)

    return _wrapped


def require_catalog_access(view):
    """Guard the routes that hand out book content: who may ask, and how often.

    Factored out of `Download`, which had the only copy of this. A session login
    counts, and so does OPDS Basic auth: e-readers fetch cover art with the same
    credentials they use for the feed and cannot carry a session cookie.

    Note this runs *outside* the ETag and the cache, so an unauthenticated
    request is turned away before it can consume either.
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        refused = authenticate_catalog(request)
        if refused is not None:
            return refused

        # After authentication, so a signed-in reader is counted as themselves
        # rather than as their address, and before the ETag and the cache, so a
        # client over its limit cannot spend anything either.
        if throttle.over_limit(request):
            return throttle.too_many(request)

        return view(request, *args, **kwargs)

    return _wrapped


def container_path(book):
    """Absolute path of the file that physically holds the book.

    That is the book file itself for a plain catalog entry, and the containing
    zip for an archived one (for INPX entries the .inpx/.inp parts of the stored
    path are not real directories and have to be stripped first).
    """
    full_path = os.path.join(config.SOPDS_ROOT_LIB, book.path)
    if book.cat_type == opdsdb.CAT_INP:
        # Убираем из пути INPX и INP файл
        inp_path, zip_name = os.path.split(full_path)
        inpx_path, inp_name = os.path.split(inp_path)
        path, inpx_name = os.path.split(inpx_path)
        full_path = os.path.join(path, zip_name)

    if book.cat_type == opdsdb.CAT_NORMAL:
        return os.path.join(full_path, book.filename)

    return full_path


def compute_content_etag(book_id, variant):
    """Validator for anything derived from a book file, without opening it.

    Deriving a cover means unzipping and parsing the book; rendering an EPUB
    means parsing every document in it. The point of the ETag is to answer a
    revalidation with 304 *before* any of that happens, so the validator uses
    only what a single `stat()` gives us: the size and mtime of the containing
    file, plus the entry that identifies the book inside it. The scanner keys
    books on (filename, path) and never refreshes the row when a file is
    replaced in place, so `Book.filesize` cannot be trusted here — the on-disk
    mtime can.

    `variant` distinguishes the different things derived from the same file (a
    cover, a thumbnail, the rendered text, one illustration) so they cannot
    collide in a shared cache.

    Returns None (no ETag, no conditional handling, no caching) when the book or
    its file is gone; those paths end in a 404 or the no-cover placeholder.
    """
    try:
        book = Book.objects.only('path', 'filename', 'cat_type').get(id=book_id)
        st = os.stat(container_path(book))
    except (Book.DoesNotExist, OSError):
        return None

    key = '%s|%s|%s|%s|%s' % (book_id, book.filename, st.st_size,
                              st.st_mtime_ns, variant)
    return '"%s"' % hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]


def content_etag(request, book_id, variant):
    """`compute_content_etag`, memoised for the duration of one request.

    The validator is needed twice per request — once by the `etag` decorator to
    answer conditional GETs, once by the view as its cache key — and each call
    costs a query plus a stat. Caching it on the request keeps that at one.
    """
    memo = (book_id, variant)
    if request is not None and getattr(request, '_content_etag_for', None) == memo:
        return request._content_etag

    value = compute_content_etag(book_id, variant)
    if request is not None:
        request._content_etag_for = memo
        request._content_etag = value

    return value


def cover_etag(request, book_id, thumbnail=False):
    return content_etag(request, book_id,
                        settings.THUMB_SIZE if thumbnail else 'full')


def read_etag(request, book_id):
    return content_etag(request, book_id, 'read')


def resource_etag(request, book_id, path):
    return content_etag(request, book_id, 'res:%s' % path)


def paged_etag(request, book_id):
    return content_etag(request, book_id, 'pdf')


@contextlib.contextmanager
def open_book_archive(book):
    """A ZipFile over the book itself.

    For a plain catalogue entry the book *is* the archive, so it is opened from
    disk and only the members actually read get decompressed. Only a book stored
    inside another zip has to be pulled into memory first — `getFileData` reads
    the whole thing, which is why this is not used unconditionally.
    """
    if book.cat_type == opdsdb.CAT_NORMAL:
        with zipfile.ZipFile(container_path(book)) as archive:
            yield archive
        return

    data = getFileData(book)
    if data is None:
        raise Http404
    with zipfile.ZipFile(data) as archive:
        yield archive


def nocover_response():
    """The placeholder image served when a book has no extractable cover."""
    if not os.path.exists(config.SOPDS_NOCOVER_PATH):
        raise Http404

    with open(config.SOPDS_NOCOVER_PATH, 'rb') as f:
        return HttpResponse(f.read(), content_type='image/jpeg')


def getFileName(book):
    if config.SOPDS_TITLE_AS_FILENAME:
        transname = utils.translit(book.title + '.' + book.format)
    else:
        transname = utils.translit(book.filename)

    return utils.to_ascii(transname)


def getFileData(book):
    full_path = os.path.join(config.SOPDS_ROOT_LIB, book.path)
    if book.cat_type==opdsdb.CAT_INP:
        # Убираем из пути INPX и INP файл
        inp_path, zip_name = os.path.split(full_path)
        inpx_path, inp_name = os.path.split(inp_path)
        path, inpx_name = os.path.split(inpx_path)
        full_path = os.path.join(path,zip_name)

    z = None
    fz = None
    s = None
    fo = None

    if book.cat_type==opdsdb.CAT_NORMAL:
        file_path=os.path.join(full_path, book.filename)
        try:
            fo=codecs.open(file_path, "rb")
            #s = fo.read()
        except FileNotFoundError:
            #s = None
            fo = None

    elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
        try:
            fz=codecs.open(full_path, "rb")
            z = open_zipfile(fz)
            fo= z.open(book.filename)
            #s=fo.read()
        except FileNotFoundError:
            #s = None
            fo = None

    if fo is None:
        if z: z.close()
        if fz: fz.close()
        return None

    dio = io.BytesIO()
    dio.write(fo.read())
    dio.seek(0)

    fo.close()
    if z: z.close()
    if fz: fz.close()

    return dio


def getFileDataZip(book):
    transname = getFileName(book)
    fo = getFileData(book)
    dio = io.BytesIO()
    zo = zipfile.ZipFile(dio, 'w', zipfile.ZIP_DEFLATED)
    zo.writestr(transname, fo.read())
    zo.close()
    dio.seek(0)

    return dio


def getFileDataConv(book, convert_type):
    if book.format != 'fb2':
       return None

    fo = getFileData(book)

    if not fo:
        return None

    (n, e) = os.path.splitext(book.filename)
    dlfilename = "%s.%s" % (n, convert_type)

    if convert_type == 'epub':
        converter_path = config.SOPDS_FB2TOEPUB
    elif convert_type == 'mobi':
        converter_path = config.SOPDS_FB2TOMOBI
    else:
        fo.close()
        return None

    tmp_fb2_path = os.path.join(config.SOPDS_TEMP_DIR, book.filename)
    tmp_conv_path = os.path.join(config.SOPDS_TEMP_DIR, dlfilename)

    try:
        fw = open(tmp_fb2_path, 'wb')
    except FileNotFoundError:
        os.mkdir(config.SOPDS_TEMP_DIR)
        fw = open(tmp_fb2_path, 'wb')

    fw.write(fo.read())
    fw.close()
    fo.close()

    popen_args = shlex.split(converter_path) + [tmp_fb2_path, config.SOPDS_TEMP_DIR]
    proc = subprocess.Popen(popen_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # У следующий строки 2 функции 1-получение информации по конвертации и 2- ожидание конца конвертации
    # В силу 2й функции ее удаление приведет к ошибке выдачи сконвертированного файла
    out = proc.stdout.readlines()

    if os.path.isfile(tmp_conv_path):
        fo = codecs.open(tmp_conv_path, "rb")
    else:
        return None

    dio = io.BytesIO()
    dio.write(fo.read())
    dio.seek(0)

    fo.close()
    os.remove(tmp_fb2_path)
    os.remove(tmp_conv_path)

    return dio


def getFileDataEpub(book):
    return getFileDataConv(book,'epub')


def getFileDataMobi(book):
    return getFileDataConv(book,'mobi')


@require_catalog_access
def Download(request, book_id, zip_flag):
    """ Загрузка файла книги """
    book = get_object_or_404(Book, id=book_id)

    if config.SOPDS_AUTH and request.user.is_authenticated:
        bookshelf.objects.get_or_create(user=request.user, book=book)

    stats.record(book.id, stats.DOWNLOADS)

    full_path=os.path.join(config.SOPDS_ROOT_LIB,book.path)
    
    if book.cat_type==opdsdb.CAT_INP:
        # Убираем из пути INPX и INP файл
        inp_path, zip_name = os.path.split(full_path)
        inpx_path, inp_name = os.path.split(inp_path)
        path, inpx_name = os.path.split(inpx_path)
        full_path = os.path.join(path,zip_name)
        
    if config.SOPDS_TITLE_AS_FILENAME:
        transname=utils.translit(book.title+'.'+book.format)
    else:
        transname=utils.translit(book.filename)
        
    transname = utils.to_ascii(transname)
        
    if zip_flag == '1':
        dlfilename=transname+'.zip'   
        content_type= Mimetype.FB2_ZIP if book.format=='fb2' else Mimetype.ZIP
    else:    
        dlfilename=transname
        content_type = mime_detector.fmt(book.format)

    response = HttpResponse()
    response["Content-Type"]='%s; name="%s"'%(content_type,dlfilename)
    response["Content-Disposition"] = 'attachment; filename="%s"'%(dlfilename)
    response["Content-Transfer-Encoding"]='binary'

    z = None
    fz = None
    s = None
    book_size = book.filesize
    if book.cat_type==opdsdb.CAT_NORMAL:
        file_path=os.path.join(full_path, book.filename)
        book_size=os.path.getsize(file_path)
        try:
            fo=codecs.open(file_path, "rb")
        except FileNotFoundError:
            raise Http404
        s=fo.read()
    elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
        try:
            fz=codecs.open(full_path, "rb")
        except FileNotFoundError:
            raise Http404
        z = open_zipfile(fz)
        book_size=z.getinfo(book.filename).file_size
        fo= z.open(book.filename)
        s=fo.read()

    if zip_flag=='1':
        dio = io.BytesIO()
        zo = zipfile.ZipFile(dio, 'w', zipfile.ZIP_DEFLATED)
        zo.writestr(transname,s)
        zo.close()
        buf = dio.getvalue()
        response["Content-Length"] = str(len(buf))
        response.write(buf)        
    else:
        response["Content-Length"] = str(book_size)
        response.write(s)

    fo.close()
    if z: z.close()
    if fz: fz.close()

    return response


def extract_cover(book, thumbnail=False):
    """Cover bytes for one book, or None when it has none we can read.

    This is the expensive part — it opens (and for an archived book, unzips) the
    file and runs a format parser over it — which is why both the ETag and the
    cache above it exist to avoid reaching here.
    """
    full_path = container_path(book)

    image = None
    try:
        if book.cat_type == opdsdb.CAT_NORMAL:
            fo = codecs.open(full_path, "rb")
            book_data = create_bookfile(fo, book.filename)
            image = book_data.extract_cover_memory()
            #fb2.parse(fo, 0)
            fo.close()
        elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
            fz = codecs.open(full_path, "rb")
            z = open_zipfile(fz)
            fo = z.open(book.filename)
            book_data = create_bookfile(fo, book.filename)
            image = book_data.extract_cover_memory()
            #fb2.parse(fo, 0)
            fo.close()
            z.close()
            fz.close()
    except Exception:
        return None

    if image and thumbnail:
        try:
            # Cover bytes are attacker-controlled; a decompression-bomb
            # image raises DecompressionBombError (Image.MAX_IMAGE_PIXELS)
            # instead of allocating huge memory. Fall back to no-cover.
            thumb = Image.open(io.BytesIO(image)).convert('RGB')
            thumb.thumbnail((settings.THUMB_SIZE, settings.THUMB_SIZE), Image.LANCZOS)
            tfile = io.BytesIO()
            thumb.save(tfile, 'JPEG')
            image = tfile.getvalue()
        except Exception:
            logger.warning('Thumbnail generation failed for book %s', book.id)
            return None

    return image or None


# Новая версия (0.42) процедуры извлечения обложек из файлов книг fb2, epub, mobi
#
# Three layers guard the extraction, cheapest first: the `etag` decorator answers
# a reader's revalidation with 304 without running the view at all; the shared
# cache below returns the bytes without touching the file; only a real miss pays
# for unzipping and parsing the book.
#
# The cache is keyed on the ETag, not on the URL as `cache_page` was: the
# validator tracks the file's mtime, so replacing a book in place now yields a
# new key instead of serving the previous cover until the TTL ran out.
@require_catalog_access
@etag(cover_etag)
def Cover(request, book_id, thumbnail=False):
    """ Загрузка обложки """
    validator = cover_etag(request, book_id, thumbnail)
    cache_key = 'sopds-cover:%s' % validator.strip('"') if validator else None

    image = cache.get(cache_key) if cache_key else None
    if image is None:
        book = get_object_or_404(Book, id=book_id)
        # b'' is the "this book has no cover" marker: caching it too keeps a
        # coverless book from being re-parsed on every page view.
        image = extract_cover(book, thumbnail) or b''
        if cache_key:
            cache.set(cache_key, image, config.SOPDS_CACHE_TIME)

    if image:
        response = HttpResponse(image, content_type='image/jpeg')
    else:
        response = nocover_response()

    # Covers carry no per-user content and are served without authentication, so
    # a shared proxy may cache them too.
    patch_cache_control(response, public=True, max_age=config.SOPDS_CACHE_TIME)

    return response


def Thumbnail(request, book_id):
    return Cover(request, book_id, True)


def NoCover(request):
    """Book-less placeholder cover, for templates that need a default image.

    The `covertmpl` route used to point straight at `Cover`, which takes a
    mandatory book_id — requesting it raised TypeError and returned 500.
    """
    response = nocover_response()
    patch_cache_control(response, public=True, max_age=config.SOPDS_CACHE_TIME)
    return response


@require_catalog_access
def ConvertFB2(request, book_id, convert_type):
    """ Выдача файла книги после конвертации в EPUB или mobi """
    book = get_object_or_404(Book, id=book_id)
    
    if book.format!='fb2':
        raise Http404

    if config.SOPDS_AUTH and request.user.is_authenticated:
        bookshelf.objects.get_or_create(user=request.user, book=book)

    stats.record(book.id, stats.DOWNLOADS)

    full_path=os.path.join(config.SOPDS_ROOT_LIB,book.path)
    if book.cat_type==opdsdb.CAT_INP:
        # Убираем из пути INPX и INP файл
        inp_path, zip_name = os.path.split(full_path)
        inpx_path, inp_name = os.path.split(inp_path)
        path, inpx_name = os.path.split(inpx_path)
        full_path = os.path.join(path,zip_name)
            
    if config.SOPDS_TITLE_AS_FILENAME:
        transname=utils.translit(book.title+'.'+book.format)
    else:
        transname=utils.translit(book.filename)      
        
    transname = utils.to_ascii(transname)
      
    (n,e)=os.path.splitext(transname)
    dlfilename="%s.%s"%(n,convert_type)
    
    if convert_type=='epub':
        converter_path=config.SOPDS_FB2TOEPUB
    elif convert_type=='mobi':
        converter_path=config.SOPDS_FB2TOMOBI
    else:
        # Defence in depth: the URL already restricts convert_type to
        # epub|mobi, but guard direct calls so converter_path can never be
        # left unbound (previously raised UnboundLocalError -> 500).
        raise Http404
    content_type=mime_detector.fmt(convert_type)

    if book.cat_type==opdsdb.CAT_NORMAL:
        tmp_fb2_path=None
        file_path=os.path.join(full_path, book.filename)
    elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
        try:
            fz=codecs.open(full_path, "rb")
        except FileNotFoundError:
            raise Http404        
        z = open_zipfile(fz)
        z.extract(book.filename,config.SOPDS_TEMP_DIR)
        tmp_fb2_path=os.path.join(config.SOPDS_TEMP_DIR,book.filename)
        file_path=tmp_fb2_path        
        
    tmp_conv_path=os.path.join(config.SOPDS_TEMP_DIR,dlfilename)
    popen_args = shlex.split(converter_path) + [file_path, tmp_conv_path]
    proc = subprocess.Popen(popen_args, stdout=subprocess.PIPE)
    #proc = subprocess.Popen((converter_path.encode('utf8'),file_path.encode('utf8'),tmp_conv_path.encode('utf8')), shell=True, stdout=subprocess.PIPE)
    out = proc.stdout.readlines()

    if os.path.isfile(tmp_conv_path):
        fo=codecs.open(tmp_conv_path, "rb")
        s=fo.read()
        # HTTP Header
        response = HttpResponse()
        response["Content-Type"]='%s; name="%s"'%(content_type,dlfilename)
        response["Content-Disposition"] = 'attachment; filename="%s"'%(dlfilename)
        response["Content-Transfer-Encoding"]='binary'    
        response["Content-Length"] = str(len(s))
        response.write(s)         
        fo.close()
    else:
        raise Http404

    try: 
        if tmp_fb2_path:
            os.remove(tmp_fb2_path)
    except Exception: 
        pass
    try: 
        os.remove(tmp_conv_path)
    except Exception: 
        pass

    return response


# Formats the flowing reader can render. FB2 goes through FB2_22_xhtml.xsl,
# EPUB through opds_catalog.epub_render; both produce the same flat stream of
# numbered paragraphs, so the reader itself does not care which it got.
READABLE_FORMATS = ('fb2', 'epub')


def viewable_formats():
    """Every format a reader can open in the browser, however it is shown.

    Two readers, because two kinds of book: the flowing one for text, the paged
    one for scans. Which paged formats count depends on the installation — DjVu
    needs a converter — so this is a function rather than a constant.
    """
    return READABLE_FORMATS + paged.viewable_formats()


@require_catalog_access
@etag(read_etag)
def Read(request, book_id):
    """Dispatch to the renderer for this book's format."""
    book = get_object_or_404(Book, id=book_id)
    if book.format == 'epub':
        return render_epub(request, book)
    return ReadFB2(request, book_id)


@require_catalog_access
@etag(paged_etag)
def PagedSource(request, book_id):
    """The book as a PDF, for the paged reader to draw.

    A PDF is sent as it is; a DjVu is converted and the result kept on disk.
    The response honours `Range`, so the reader fetches the pages it is showing
    rather than the whole of a scan before the first one appears.
    """
    book = get_object_or_404(Book, id=book_id)
    if book.format not in paged.viewable_formats():
        raise Http404

    # Only on the opening request. Reading a scan is a long series of Range
    # requests for the same book, and each one would otherwise cost a query to
    # rediscover that it is already on the shelf.
    if (config.SOPDS_AUTH and request.user.is_authenticated
            and not request.headers.get('Range')):
        bookshelf.objects.get_or_create(user=request.user, book=book)

    path = paged.materialise(book, paged_etag(request, book_id), container_path(book))
    if path is None:
        raise Http404

    (name, _ext) = os.path.splitext(getFileName(book))
    return paged.serve(request, path, '%s.pdf' % name)


def render_epub(request, book):
    """ Чтение EPUB в браузере. Not a route: reached through `Read`.

    Rendering means unzipping the book and parsing, sanitising and serialising
    every document in its spine — a few hundred kilobytes of HTML out of dozens
    of parses. Doing that per page view is what the cache is for; it is keyed on
    the same content validator as the ETag, so replacing the file on disk
    invalidates the render at once.
    """
    if config.SOPDS_AUTH and request.user.is_authenticated:
        bookshelf.objects.get_or_create(user=request.user, book=book)

    validator = read_etag(request, book.id)
    cache_key = 'sopds-epub:%s' % validator.strip('"') if validator else None

    html = cache.get(cache_key) if cache_key else None
    if html is None:
        def resource_url(path):
            return reverse('opds_catalog:readres',
                           kwargs={'book_id': book.id, 'path': path})
        try:
            with open_book_archive(book) as archive:
                html = epub_render.render_archive(archive, resource_url)
        except (epub_render.EpubError, zipfile.BadZipFile, KeyError, OSError) as err:
            logger.warning('Cannot render EPUB for book %s: %s', book.id, err)
            raise Http404
        if cache_key and len(html) <= MAX_CACHED_RENDER_BYTES:
            cache.set(cache_key, html, config.SOPDS_CACHE_TIME)

    response = HttpResponse(html, content_type='text/html; charset=utf-8')
    patch_cache_control(response, private=True, max_age=config.SOPDS_CACHE_TIME)
    return response


@require_catalog_access
@etag(resource_etag)
def ReadResource(request, book_id, path):
    """Serve one image from inside an EPUB, for the rendered reader page.

    Only images the package actually contains are served, and only as images:
    the path is resolved against the archive's own name list, so a crafted
    `src` cannot address anything outside it, and the content type is decided
    here rather than taken from the book.
    """
    book = get_object_or_404(Book, id=book_id)
    if book.format != 'epub':
        raise Http404

    # normpath collapses any ".." before the lookup, and membership in the
    # archive is the authorisation: a name that is not in it is a 404.
    wanted = posixpath.normpath(path).lstrip('/')

    validator = resource_etag(request, book_id, wanted)
    cache_key = 'sopds-epubres:%s' % validator.strip('"') if validator else None

    cached = cache.get(cache_key) if cache_key else None
    if cached is not None:
        payload, content_type = cached
    else:
        content_type = mimetypes.guess_type(wanted)[0] or ''
        if not content_type.startswith('image/'):
            raise Http404
        try:
            with open_book_archive(book) as archive:
                info = next((i for i in archive.infolist() if i.filename == wanted), None)
                if info is None or info.file_size > MAX_RESOURCE_BYTES:
                    raise Http404
                payload = archive.read(info)
        except (zipfile.BadZipFile, OSError):
            raise Http404

        # A page of illustrations is a burst of requests for the same archive,
        # so caching the small ones turns the burst into one read. Large plates
        # are served straight through rather than filling the cache with them.
        if cache_key and len(payload) <= MAX_CACHED_RESOURCE_BYTES:
            cache.set(cache_key, (payload, content_type), config.SOPDS_CACHE_TIME)

    response = HttpResponse(payload, content_type=content_type)
    patch_cache_control(response, private=True, max_age=config.SOPDS_CACHE_TIME)
    return response


@require_catalog_access
def ReadFB2(request, book_id):
    """ Загрузка книги """
    book = get_object_or_404(Book, id=book_id)

    # FB2_22_xhtml.xsl only understands FB2; anything else reached ET.parse()
    # and died there with a 500 (an EPUB or MOBI is a binary container, not
    # XML). Mirror the guard ConvertFB2 already has.
    if book.format != 'fb2':
        raise Http404

    if config.SOPDS_AUTH and request.user.is_authenticated:
        bookshelf.objects.get_or_create(user=request.user, book=book)

    full_path = os.path.join(config.SOPDS_ROOT_LIB, book.path)

    if book.cat_type == opdsdb.CAT_INP:
        # Убираем из пути INPX файл
        inpx_path, zip_name = os.path.split(full_path)
        path, inpx_file = os.path.split(inpx_path)
        if search(r'.*\.inpx$', path):
            path, _ = os.path.split(path)
        full_path = os.path.join(path, zip_name)

    if config.SOPDS_TITLE_AS_FILENAME:
        transname=utils.translit(book.title+'.'+book.format)
    else:
        transname=utils.translit(book.filename)
        
    transname = utils.to_ascii(transname)

    dlfilename=transname
    content_type = mime_detector.fmt(book.format)

    response = HttpResponse()
    response["Content-Type"]='text/html; charset=utf-8'

    z = None
    fz = None
    s = None
    book_size = book.filesize
    if book.cat_type==opdsdb.CAT_NORMAL:
        file_path=os.path.join(full_path, book.filename)
        book_size=os.path.getsize(file_path)
        try:
            fo=codecs.open(file_path, "rb")
        except FileNotFoundError:
            raise Http404
        # NB: do not read fo here — ET.parse(fo) below needs it at position 0.
    elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
        try:
            fz=codecs.open(full_path, "rb")
        except FileNotFoundError:
            raise Http404
        z = open_zipfile(fz)
        book_size=z.getinfo(book.filename).file_size
        fo= z.open(book.filename)

    # Untrusted book XML: parse with entity resolution/DTD/network disabled
    # (XXE / billion-laughs guard). The stylesheet below is our own trusted file.
    dom = ET.parse(fo, parser=safe_lxml_parser())
    xslt = ET.parse('%s/FB2_22_xhtml.xsl' % os.path.dirname(os.path.realpath(__file__)))
    transform = ET.XSLT(xslt)
    newdom = transform(dom)
    book_content = ET.tostring(newdom, pretty_print=True)

    response.write(book_content)

    fo.close()
    if z: z.close()
    if fz: fz.close()

    return response
