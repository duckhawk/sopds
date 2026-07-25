"""A minimal, per-user WebDAV endpoint for Moon+ Reader Pro cloud sync.

Moon+ Reader uses WebDAV as a dumb file store: it PROPFINDs a directory, then
GET/PUTs its own position (``.po``) and backup files there. This server does not
interpret any of that — it stores each user's files under a private directory
(``SOPDS_WEBDAV_ROOT/<user id>/``) and never exposes the book library, so there
is no path into ``SOPDS_ROOT_LIB``.

Only the verbs Moon+ Reader (and typical WebDAV clients) need are implemented:
OPTIONS, PROPFIND, GET, HEAD, PUT, DELETE, MKCOL, MOVE, COPY, and no-op
LOCK/UNLOCK (advertised as class 2 so lock-requiring clients proceed). All
requests require HTTP Basic auth against the SOPDS/OIDC accounts and are
CSRF-exempt. The endpoint 404s while ``SOPDS_WEBDAV_ENABLE`` is off.
"""
import os
import shutil
from email.utils import formatdate
from urllib.parse import quote, unquote, urlparse
from xml.sax.saxutils import escape

from django.http import HttpResponse, FileResponse, Http404
from django.views.decorators.csrf import csrf_exempt

from constance import config

from .auth import authenticate_basic, basic_auth_challenge

DAV_METHODS = ['OPTIONS', 'HEAD', 'GET', 'PUT', 'DELETE', 'MKCOL',
               'PROPFIND', 'PROPPATCH', 'MOVE', 'COPY', 'LOCK', 'UNLOCK']


def _user_root(user):
    """Absolute path to (and creating) the calling user's private DAV area."""
    root = os.path.abspath(os.path.join(config.SOPDS_WEBDAV_ROOT, str(user.id)))
    os.makedirs(root, exist_ok=True)
    return root


def _resolve(user_root, path):
    """Map a URL sub-path to an absolute path confined to ``user_root``.

    Anchoring on a leading '/' before normpath collapses any ``..`` so a client
    can never escape its own directory; the startswith check is a belt-and-braces
    guard. Returns ``None`` if the path would escape.
    """
    rel = os.path.normpath('/' + unquote(path)).lstrip('/')
    full = os.path.abspath(os.path.join(user_root, rel)) if rel else user_root
    if full != user_root and not full.startswith(user_root + os.sep):
        return None
    return full


def _href(request_path, is_dir):
    """URL-quoted href for a resource, with a trailing slash for collections."""
    href = quote(request_path)
    if is_dir and not href.endswith('/'):
        href += '/'
    return href


def _prop_xml(href, full_path):
    """A single <D:response> block describing one resource."""
    is_dir = os.path.isdir(full_path)
    stat = os.stat(full_path)
    lastmod = formatdate(stat.st_mtime, usegmt=True)
    created = formatdate(stat.st_ctime, usegmt=True)
    if is_dir:
        resourcetype = '<D:collection/>'
        length = ''
    else:
        resourcetype = ''
        length = '<D:getcontentlength>%d</D:getcontentlength>' % stat.st_size
    etag = '"%x-%x"' % (int(stat.st_mtime), stat.st_size)
    return (
        '<D:response>'
        '<D:href>%s</D:href>'
        '<D:propstat><D:prop>'
        '<D:resourcetype>%s</D:resourcetype>'
        '%s'
        '<D:getlastmodified>%s</D:getlastmodified>'
        '<D:creationdate>%s</D:creationdate>'
        '<D:getetag>%s</D:getetag>'
        '</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>'
        '</D:response>'
    ) % (escape(_href(href, is_dir)), resourcetype, length,
         escape(lastmod), escape(created), escape(etag))


def _multistatus(body):
    xml = ('<?xml version="1.0" encoding="utf-8"?>'
           '<D:multistatus xmlns:D="DAV:">%s</D:multistatus>') % body
    resp = HttpResponse(xml.encode('utf-8'), status=207,
                        content_type='application/xml; charset=utf-8')
    return resp


def _handle_propfind(request, full, request_path):
    if not os.path.exists(full):
        raise Http404()
    depth = request.META.get('HTTP_DEPTH', 'infinity')
    parts = [_prop_xml(request_path, full)]
    if os.path.isdir(full) and depth != '0':
        base = request_path if request_path.endswith('/') else request_path + '/'
        for name in sorted(os.listdir(full)):
            child = os.path.join(full, name)
            parts.append(_prop_xml(base + name, child))
    return _multistatus(''.join(parts))


def _handle_put(full):
    parent = os.path.dirname(full)
    if not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    existed = os.path.exists(full)
    return existed


def _dest_path(request, user_root):
    """Resolve the Destination header (for MOVE/COPY) to a confined path."""
    dest = request.META.get('HTTP_DESTINATION')
    if not dest:
        return None
    dest_path = urlparse(dest).path
    # Strip the /dav/ mount prefix, keeping the sub-path the client addressed.
    marker = '/dav/'
    idx = dest_path.find(marker)
    sub = dest_path[idx + len(marker):] if idx != -1 else dest_path.lstrip('/')
    return _resolve(user_root, sub)


@csrf_exempt
def dav(request, path=''):
    if not config.SOPDS_WEBDAV_ENABLE:
        raise Http404('webdav disabled')

    user = authenticate_basic(request)
    if user is None:
        return basic_auth_challenge()

    user_root = _user_root(user)
    full = _resolve(user_root, path)
    if full is None:
        return HttpResponse(status=403)

    method = request.method.upper()
    request_path = request.path  # already includes the /dav/ prefix

    if method == 'OPTIONS':
        resp = HttpResponse(status=200)
        resp['Allow'] = ', '.join(DAV_METHODS)
        resp['DAV'] = '1, 2'
        resp['MS-Author-Via'] = 'DAV'
        return resp

    if method == 'PROPFIND':
        return _handle_propfind(request, full, request_path)

    if method == 'PROPPATCH':
        # We store no custom dead properties; acknowledge so clients proceed.
        if not os.path.exists(full):
            raise Http404()
        return _multistatus(
            '<D:response><D:href>%s</D:href>'
            '<D:propstat><D:prop/><D:status>HTTP/1.1 200 OK</D:status></D:propstat>'
            '</D:response>' % escape(_href(request_path, os.path.isdir(full))))

    if method in ('GET', 'HEAD'):
        if not os.path.exists(full):
            raise Http404()
        if os.path.isdir(full):
            # A bare listing is enough; Moon+ Reader navigates via PROPFIND.
            names = '\n'.join(sorted(os.listdir(full)))
            resp = HttpResponse(names, content_type='text/plain; charset=utf-8')
            return resp
        if method == 'HEAD':
            resp = HttpResponse(status=200)
            resp['Content-Length'] = str(os.path.getsize(full))
            return resp
        return FileResponse(open(full, 'rb'))

    if method == 'PUT':
        if os.path.isdir(full):
            return HttpResponse(status=405)
        existed = _handle_put(full)
        # Stream straight from the request body. Reading via request.read()
        # (rather than request.body) avoids DATA_UPLOAD_MAX_MEMORY_SIZE, so full
        # Moon+ Reader library backups — which exceed the 2.5 MB default — upload.
        with open(full, 'wb') as fh:
            while True:
                chunk = request.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
        return HttpResponse(status=204 if existed else 201)

    if method == 'DELETE':
        if not os.path.exists(full):
            raise Http404()
        if full == user_root:
            return HttpResponse(status=403)
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)
        return HttpResponse(status=204)

    if method == 'MKCOL':
        if os.path.exists(full):
            return HttpResponse(status=405)
        if not os.path.isdir(os.path.dirname(full)):
            return HttpResponse(status=409)  # missing intermediate collection
        os.makedirs(full)
        return HttpResponse(status=201)

    if method in ('MOVE', 'COPY'):
        if not os.path.exists(full):
            raise Http404()
        dest = _dest_path(request, user_root)
        if dest is None:
            return HttpResponse(status=400)
        overwrite = request.META.get('HTTP_OVERWRITE', 'T').upper() != 'F'
        dest_existed = os.path.exists(dest)
        if dest_existed and not overwrite:
            return HttpResponse(status=412)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if dest_existed:
            if os.path.isdir(dest):
                shutil.rmtree(dest)
            else:
                os.remove(dest)
        if method == 'MOVE':
            shutil.move(full, dest)
        elif os.path.isdir(full):
            shutil.copytree(full, dest)
        else:
            shutil.copy2(full, dest)
        return HttpResponse(status=204 if dest_existed else 201)

    if method == 'LOCK':
        # No real locking; hand back a synthetic token so class-2 clients proceed.
        token = 'opaquelocktoken:sopds-%s' % quote(path or 'root')
        xml = ('<?xml version="1.0" encoding="utf-8"?>'
               '<D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
               '<D:locktype><D:write/></D:locktype>'
               '<D:lockscope><D:exclusive/></D:lockscope>'
               '<D:depth>infinity</D:depth>'
               '<D:timeout>Second-3600</D:timeout>'
               '<D:locktoken><D:href>%s</D:href></D:locktoken>'
               '</D:activelock></D:lockdiscovery></D:prop>') % escape(token)
        resp = HttpResponse(xml.encode('utf-8'), status=200,
                            content_type='application/xml; charset=utf-8')
        resp['Lock-Token'] = '<%s>' % token
        return resp

    if method == 'UNLOCK':
        return HttpResponse(status=204)

    resp = HttpResponse(status=405)
    resp['Allow'] = ', '.join(DAV_METHODS)
    return resp
