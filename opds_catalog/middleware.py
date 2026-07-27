import base64
import binascii

from django.conf import settings
from django.http import HttpResponse
from django.contrib import auth
from django.utils import translation
from django.middleware.cache import FetchFromCacheMiddleware as DjangoFetchFromCacheMiddleware
from django.utils.deprecation import MiddlewareMixin

from constance import config


class BasicAuthMiddleware(object):
    header = "HTTP_AUTHORIZATION"

    def unauthed(self):
        response = HttpResponse("""<html><title>Auth required</title><body>
                                <h1>Authorization Required</h1></body></html>""", content_type="text/html")
        response['WWW-Authenticate'] = 'Basic realm="OPDS"'
        response.status_code = 401
        return response

    def process_request(self,request):
        if not config.SOPDS_AUTH:
            return
            
        # AuthenticationMiddleware is required so that request.user exists.
        #if not hasattr(request, 'user'):
        #    raise ImproperlyConfigured(
        #        "The Django remote user auth middleware requires the"
        #        " authentication middleware to be installed.  Edit your"
        #        " MIDDLEWARE setting to insert"
        #        " 'django.contrib.auth.middleware.AuthenticationMiddleware'"
        #        " before the BasicAuthMiddleware class.")
        try:
            authentication = request.META[self.header]
        except KeyError:
            return self.unauthed()  
                    
        try:
            (auth_meth, auth_data) = authentication.split(' ',1)
        except ValueError:
            return self.unauthed()  

        if 'basic' != auth_meth.lower():
            return self.unauthed()

        # A malformed credential blob (bad base64, non-utf8 bytes, or no ':'
        # separator) is a bad request, not a server error: answer 401 instead
        # of letting binascii.Error / UnicodeDecodeError / ValueError 500.
        try:
            auth_data = base64.b64decode(auth_data.strip()).decode('utf-8')
            username, password = auth_data.split(':', 1)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return self.unauthed()

        user = auth.authenticate(username=username, password=password)
        if not (user and user.is_active):
            # No local match: for OPDS clients (Basic auth only), validate the
            # credentials against Keycloak via the ROPC grant so OIDC users can
            # read feeds without a local password.
            from sopds_web_backend import oidc
            if oidc.oidc_enabled():
                user = oidc.authenticate_password(username, password)

        if user and user.is_active:
            # OPDS clients (e-readers) re-send Basic auth on every request. Do
            # NOT auth.login() here: that created and persisted a new session
            # row per request, growing django_session without bound. Setting
            # request.user is all the feed/download views behind this need.
            request.user = user
            return request

        return self.unauthed()


# Where a visitor's own choice of language is kept. Not Django's own
# LocaleMiddleware key: that middleware is not in the stack, because the
# language here is a setting an administrator makes rather than something
# negotiated from Accept-Language.
LANGUAGE_SESSION_KEY = 'sopds_language'


class SOPDSLocaleMiddleware(MiddlewareMixin):
    """Activate the interface language for this request.

    Normally that is whatever the administrator chose, the same for everyone.
    Where the switcher is turned on a visitor may override it for their own
    session — on a public site, readers do not share a first language.
    """

    def process_request(self, request):
        request.LANG = self.language_for(request)
        translation.activate(request.LANG)
        request.LANGUAGE_CODE = request.LANG

    @staticmethod
    def language_for(request):
        site_default = config.SOPDS_LANGUAGE
        if not config.SOPDS_LANGUAGE_SWITCHER:
            return site_default

        # Validated against the offered set: whatever is in the session was put
        # there by this application, but a stale value from before a language
        # was removed must not be activated.
        chosen = request.session.get(LANGUAGE_SESSION_KEY) if hasattr(request, 'session') else None
        offered = {code for code, _name in settings.SITE_LANGUAGES}
        return chosen if chosen in offered else site_default

class FetchFromCacheMiddleware(DjangoFetchFromCacheMiddleware):

    def process_request(self, request):
        if not request.user.is_authenticated:
            return None
        else:
            return super(FetchFromCacheMiddleware, self).process_request(request)