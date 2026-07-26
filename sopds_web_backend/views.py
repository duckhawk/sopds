from functools import wraps
from random import randint

from django.shortcuts import render, redirect, get_object_or_404
from django.template.context_processors import csrf
from django.core.cache import cache
from django.db.models import Count, Min, Max, Prefetch
from django.utils.translation import gettext as _
from django.contrib.auth import authenticate, login, logout, REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import user_passes_test
from django.views.decorators.vary import vary_on_headers
from django.urls import reverse, reverse_lazy
from django.utils.html import strip_tags
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import HttpResponseForbidden, HttpResponseRedirect


from opds_catalog import models
from opds_catalog.models import Book, Author, Series, bookshelf, Counter, Catalog, Genre, lang_menu, Theme
from opds_catalog import settings
from opds_catalog.utils import alphabet_menu, contains_page_ids, contains_page
from book_tools.format.util import normalize_isbn
from opds_catalog import dl, ratings, stats
from constance import config
from sopds_web_backend import oidc
from opds_catalog.opds_paginator import Paginator as OPDS_Paginator


from sopds_web_backend.settings import HALF_PAGES_LINKS
from django.http import HttpResponse, JsonResponse, Http404


def _int_param(request, name, default=0):
    """Parse an integer GET parameter, falling back to `default` on a missing
    or non-numeric value (instead of raising ValueError -> HTTP 500)."""
    try:
        return int(request.GET.get(name, default))
    except (TypeError, ValueError):
        return default


DEFAULT_THEME_CSS = "css/sopds.css"


def theme_css(user):
    """Return the user's theme key in a single query (instead of exists()+get()).

    The stored values ("css/sopds.css" / "css/sopds-dark.css") are now opaque
    theme keys, not real files: the single lectern.css stylesheet is always
    loaded and sopds_main.html selects light/dark via `data-theme` from whether
    this key contains "dark".

    An anonymous visitor (which every visitor is when SOPDS_AUTH is off) has no
    row and cannot be used as a filter value, so they get the default.
    """
    if not user.is_authenticated:
        return DEFAULT_THEME_CSS

    theme = Theme.objects.filter(user=user).first()
    return theme.theme_css if theme else DEFAULT_THEME_CSS


def user_prefs(user):
    """Per-user preferences row (theme + reader settings), created on demand.

    Anonymous visitors get an unsaved row of defaults: there is nobody to own
    the preferences, but the reader still has to render.
    """
    if not user.is_authenticated:
        return Theme(theme_css=DEFAULT_THEME_CSS)

    prefs, _ = Theme.objects.get_or_create(user=user)
    return prefs


def personal_view(function):
    """Guard for views that read or write one specific user's rows.

    `sopds_login` deliberately lets everyone through when SOPDS_AUTH is off, and
    that is right for browsing the catalogue. It is not right for the bookshelf,
    the theme toggle or the sync password: those filter `Theme`/`bookshelf`/
    `KosyncCredential` by `request.user`, and an `AnonymousUser` is not a value
    the ORM can filter on — every one of them raised TypeError and returned 500.
    With authentication off there is no user to own that data, so answer 403.
    """
    @wraps(function)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return HttpResponseForbidden(_('This page requires a signed-in user.'))
        return function(request, *args, **kwargs)

    return _wrapped


def sopds_login(function=None, redirect_field_name=REDIRECT_FIELD_NAME, url=None):
    actual_decorator = user_passes_test(
        lambda u: (u.is_authenticated if config.SOPDS_AUTH else True),
        login_url=reverse_lazy(url),
        redirect_field_name=redirect_field_name
    ) 
    if function:
        return actual_decorator(function)
    return actual_decorator


def sopds_processor(request):
    args={}
    args['app_title'] = settings.TITLE
    args['sopds_auth'] = config.SOPDS_AUTH
    args['oidc_enabled'] = oidc.oidc_enabled()
    args['oidc_button_text'] = config.SOPDS_OIDC_BUTTON_TEXT
    args['sopds_version'] = settings.VERSION
    args['alphabet'] = config.SOPDS_ALPHABET_MENU
    args['splititems'] = config.SOPDS_SPLITITEMS
    args['fb2tomobi'] = (config.SOPDS_FB2TOMOBI != "")
    args['fb2toepub'] = (config.SOPDS_FB2TOEPUB != "")
    args['nozip'] = settings.NOZIP_FORMATS
    args['cache_t'] = 0

    if config.SOPDS_ALPHABET_MENU:
        args['lang_menu'] = lang_menu
    
    if config.SOPDS_AUTH:
        user = request.user
        if user.is_authenticated:
            result = []
            shelf = (bookshelf.objects.filter(user=user)
                     .select_related('book')
                     .prefetch_related('book__authors')
                     .order_by('-readtime')[:8])
            for row in shelf:
                book = row.book
                p = {'id': row.id, 'readtime': row.readtime, 'book_id': row.book_id, 'title': book.title,
                     'authors': [{'id': a.id, 'full_name': a.full_name} for a in book.authors.all()]}
                result.append(p)
            args['bookshelf'] = result

    # The stats block and random book are identical for every user and expensive
    # to compute (a COUNT over all books + a random row lookup), so they run on
    # every single page render. Cache them for a short period instead.
    common = cache.get('sopds_processor_common')
    if common is None:
        books_count = Counter.objects.get_counter(models.counter_allbooks)
        random_book = None
        if books_count:
            # Avoid LIMIT 1 OFFSET N on a huge table (Postgres physically walks
            # N rows). Pick a random id and take the first existing row from it.
            max_id = Book.objects.aggregate(m=Max('id'))['m'] or 0
            if max_id:
                random_book = (Book.objects.filter(id__gte=randint(1, max_id)).order_by('id').first()
                               or Book.objects.order_by('-id').first())
        stats = {d['name']: d['value'] for d in Counter.obj.all().values()}
        stats['lastscan_date'] = Counter.objects.get_lastscan()
        common = {'random_book': random_book, 'stats': stats}
        cache.set('sopds_processor_common', common, 60)

    args['random_book'] = common['random_book']
    args['stats'] = common['stats']

    return args


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def SearchBooksView(request):
    #Read searchtype, searchterms, searchterms0, page from form
    args = {}
    args.update(csrf(request))

    if request.GET:
        searchtype = request.GET.get('searchtype', 'm')
        searchterms = request.GET.get('searchterms', '')
        #searchterms0 = int(request.POST.get('searchterms0', ''))
        page_num = _int_param(request, 'page', 1)
        page_num = page_num if page_num>0 else 1
        
        #if (len(searchterms)<3) and (searchtype in ('m', 'b', 'e')):
        #    args['errormsg'] = 'Too few symbols in search string !';
        #    return render_to_response('sopds_error.html', args)
        
        # Pasting an ISBN into the (title) search box should find the book, not
        # nothing: when the term is a checksum-valid ISBN, search that field.
        # A title never normalises to a valid ISBN, so this cannot shadow a real
        # title search.
        if searchtype == 'm' and normalize_isbn(searchterms):
            searchtype = 'x'

        if searchtype == 'm':
            #books = Book.objects.extra(where=["upper(title) like %s"], params=["%%%s%%"%searchterms.upper()]).order_by('title','-docdate')
            books = Book.objects.filter(search_title__contains=searchterms.upper()).order_by('search_title','-docdate')
            args['breadcrumbs'] = [_('Books'),_('Search by title'),searchterms]
            args['searchobject'] = 'title'

        # Поиск книг по ISBN
        elif searchtype == 'x':
            isbn = normalize_isbn(searchterms)
            books = (Book.objects.filter(isbn=isbn) if isbn else Book.objects.none())
            books = books.order_by('search_title', '-docdate')
            args['breadcrumbs'] = [_('Books'), _('Search by ISBN'), isbn or searchterms]
            args['searchobject'] = 'title'

        if searchtype == 'b':
            #books = Book.objects.extra(where=["upper(title) like %s"], params=["%s%%"%searchterms.upper()]).order_by('title','-docdate')
            books = Book.objects.filter(search_title__startswith=searchterms.upper()).order_by('search_title','-docdate')
            args['breadcrumbs'] = [_('Books'),_('Search by title'),searchterms]   
            args['searchobject'] = 'title'         
            
        elif searchtype == 'a':
            try:
                author_id = int(searchterms)
                author = Author.objects.get(id=author_id)
                #aname = "%s %s"%(author.last_name,author.first_name)
                aname = author.full_name
            except:
                author_id = 0
                aname = ""                  
            books = Book.objects.filter(authors=author_id).order_by('search_title','-docdate')  
            args['breadcrumbs'] = [_('Books'),_('Search by author'),aname]   
            args['searchobject'] = 'author' 
            
        # Поиск книг по серии
        elif searchtype == 's':
            try:
                ser_id = int(searchterms)
                ser = Series.objects.get(id=ser_id).ser
            except:
                ser_id = 0
                ser = ""
            #books = Book.objects.filter(series=ser_id).order_by('search_title','-docdate')
            books = Book.objects.filter(series=ser_id).order_by('bseries__ser_no','search_title','-docdate')
            args['breadcrumbs'] = [_('Books'),_('Search by series'),ser]
            args['searchobject'] = 'series'
            
        # Поиск книг по жанру
        elif searchtype == 'g':
            try:
                genre_id = int(searchterms)
                section = Genre.objects.get(id=genre_id).section
                subsection = Genre.objects.get(id=genre_id).subsection
                args['breadcrumbs'] = [_('Books'),_('Search by genre'),section,subsection]
            except:
                genre_id = 0
                args['breadcrumbs'] = [_('Books'),_('Search by genre')]
                
            books = Book.objects.filter(genres=genre_id).order_by('search_title','-docdate') 
            args['searchobject'] = 'genre'
                                   
        # Недавно добавленные книги (searchterms не используется)
        elif searchtype == 'n':
            books = Book.objects.all().order_by('-registerdate', '-id')
            args['breadcrumbs'] = [_('Books'), _('Recently added')]
            args['searchobject'] = 'title'

        # Книги с лучшей оценкой сообщества (searchterms не используется)
        elif searchtype == 'r':
            books = ratings.top_rated()
            args['breadcrumbs'] = [_('Books'), _('Top rated')]
            args['searchobject'] = 'title'

        # Самые скачиваемые книги (searchterms не используется)
        elif searchtype == 'p':
            books = stats.most_popular()
            args['breadcrumbs'] = [_('Books'), _('Most popular')]
            args['searchobject'] = 'title'

        # Поиск книг на книжной полке
        elif searchtype == 'u':
            if config.SOPDS_AUTH:
                books = Book.objects.filter(bookshelf__user=request.user).order_by('-bookshelf__readtime')
                args['breadcrumbs'] = [_('Books'),_('Bookshelf'),request.user.username]
                #books = bookshelf.objects.filter(user=request.user).select_related('book')

                # Reading status is set by hand and, since #71, by an e-reader
                # syncing progress — but the shelf could only ever be shown
                # whole. Filter it: ?searchtype=u&status=reading. An unknown
                # value is ignored rather than yielding an empty shelf.
                status = request.GET.get('status', '')
                if status in {c[0] for c in bookshelf.STATUS_CHOICES if c[0]}:
                    books = books.filter(bookshelf__user=request.user,
                                         bookshelf__status=status)
                    args['shelf_status'] = status
                    args['breadcrumbs'].append(dict(bookshelf.STATUS_CHOICES)[status])
            else:
                books = Book.objects.filter(id=0)
                args['breadcrumbs'] = [_('Books'), _('Bookshelf')]
            args['searchobject'] = 'title'
            args['isbookshelf'] = 1
                
        # Поиск дубликатов для книги            
        elif searchtype == 'd':
            try:
                book_id = int(searchterms)
            except (TypeError, ValueError):
                raise Http404
            mbook = get_object_or_404(Book, id=book_id)
            books = Book.objects.filter(title=mbook.title, authors__in=mbook.authors.all()).exclude(id=book_id).distinct().order_by('-docdate')
            args['breadcrumbs'] = [_('Books'),_('Doubles for book'),mbook.title]
            args['searchobject'] = 'title'
            
        # Поиск книги по ID. Хотел найти еще и дубликаты к книге, но почему-то не работает запрос правильно. Ума не приложу почему.    
        elif searchtype == 'i':
            try:
                book_id = int(searchterms)
                #mbook = Book.objects.get(id=book_id)
            except:
                book_id = 0
                #mbook = None
            books = Book.objects.filter(id=book_id)
            try:
                args['breadcrumbs'] = [_('Books'),books[0].title]
            except IndexError:
                args['breadcrumbs'] = [_('Books')]
            #books = Book.objects.filter(title=mbook.title, authors__in=mbook.authors.all()).distinct().order_by('-docdate')                
            #args['breadcrumbs'] = [_('Books'),mbook.title]
            args['searchobject'] = 'title'
        
        # prefetch_related on sqlite on items >999 therow error "too many SQL variables"    
        #if len(books)>0:
        #    books = books.select_related('authors','genres','series')

        # Добавляем Left Join с таблицей BookShelfб чтобы вытащить дату прочтения книги из книжной полки
        #books = books.filter(Q(bookshelf__isnull=True)|Q(bookshelf__user=request.user))
        #books = books.prefetch_related('bookshelf_set')
        #print(books.query)


        # Фильтруем дубликаты и формируем выдачу затребованной страницы
        books_count = books.count()
        op = OPDS_Paginator(books_count, 0, page_num, config.SOPDS_MAXITEMS, HALF_PAGES_LINKS)
        items = []
        
        prev_title = ''
        prev_authors_set = set()
        
        # Начаинам анализ с последнего элемента на предыдущей странице, чторбы он "вытянул" с этой страницы
        # свои дубликаты если они есть
        summary_DOUBLES_HIDE = config.SOPDS_DOUBLES_HIDE and (searchtype != 'd')
        start = op.d1_first_pos if ((op.d1_first_pos == 0) or (not summary_DOUBLES_HIDE)) else op.d1_first_pos-1
        finish = op.d1_last_pos

        # Prefetch related rows for the whole page in a handful of queries instead
        # of ~6 queries per book (authors/genres/series/ser_no/bookshelf x2).
        prefetch = ['authors', 'genres', 'series', 'bseries_set']
        if config.SOPDS_AUTH:
            prefetch.append(Prefetch('bookshelf_set',
                                     queryset=bookshelf.objects.filter(user=request.user),
                                     to_attr='user_shelf'))
        # For the title-substring search, fetch the page (plus a small lookahead
        # for the doubles pass) via the pg_trgm-friendly fenced query instead of
        # slicing an ORDER BY queryset, which makes PostgreSQL scan the btree
        # row-by-row (very slow on large cyrillic catalogs).
        lookahead_rows = None
        if searchtype == 'm' and searchterms:
            want = finish - start + 1
            page_ids = contains_page_ids(
                'opds_catalog_book', 'search_title', searchterms.upper(),
                'search_title, docdate', 'search_title, docdate DESC',
                want + 30, start)
            by_id = {b.id: b for b in Book.objects.filter(id__in=page_ids).prefetch_related(*prefetch)}
            fetched = [by_id[i] for i in page_ids if i in by_id]
            page_rows = fetched[:want]
            lookahead_rows = fetched[want:]
        else:
            page_rows = books.prefetch_related(*prefetch)[start:finish+1]

        # One query for the whole page, keyed on ids we already have.
        page_ratings = ratings.summary(r.id for r in page_rows)
        page_stats = stats.summary(r.id for r in page_rows)

        for row in page_rows:
            user_shelf = getattr(row, 'user_shelf', []) if config.SOPDS_AUTH else []
            p = {'doubles': 0,
                 'lang_code': row.lang_code,
                 'filename': row.filename,
                 'path': row.path,
                 'registerdate': row.registerdate,
                 'id': row.id,
                 'annotation': strip_tags(row.annotation),
                 'docdate': row.docdate,
                 'format': row.format,
                 'title': row.title,
                 'isbn': row.isbn,
                 'publisher': row.publisher,
                 'filesize': row.filesize // 1000,
                 'authors': row.authors.all(),
                 'genres': row.genres.all(),
                 'series': row.series.all(),
                 'ser_no': row.bseries_set.all(),
                 'bookshelf': bool(user_shelf),
                 'readtime': user_shelf if config.SOPDS_AUTH else None,
                 'status': user_shelf[0].status if user_shelf else '',
                 'rating': user_shelf[0].rating if user_shelf else None,
                 # The user's own star count, and what everyone else made of it.
                 'rating_all': page_ratings.get(row.id),
                 'stat': page_stats.get(row.id),
                 'readable': row.format in dl.READABLE_FORMATS,
                 # Percentage read, as reported by an e-reader over kosync.
                 'percent': user_shelf[0].percent if user_shelf else None
                 }

            if summary_DOUBLES_HIDE:
                title = p['title']
                authors_set = {a.id for a in p['authors']}
                if title.upper() == prev_title.upper() and authors_set == prev_authors_set:
                    items[-1]['doubles'] += 1
                    if p['bookshelf']:
                        items[-1]['bookshelf'] = True
                else:
                    items.append(p)                   
                prev_title = title
                prev_authors_set = authors_set
            else:
                items.append(p)
        # "вытягиваем" дубликаты книг со следующей страницы и удаляем первый элемент который с предыдущей страницы и "вытягивал" дубликаты с текущей
        if summary_DOUBLES_HIDE:
            double_flag = True
            if lookahead_rows is not None:
                for nb in lookahead_rows:
                    if not double_flag:
                        break
                    if nb.title.upper() == prev_title.upper() and {a.id for a in nb.authors.all()} == prev_authors_set:
                        items[-1]['doubles'] += 1
                    else:
                        double_flag = False
            else:
                while ((finish+1)<books_count) and double_flag:
                    finish += 1
                    if books[finish].title.upper() == prev_title.upper() and {a['id'] for a in books[finish].authors.values()} == prev_authors_set:
                        items[-1]['doubles'] += 1
                    else:
                        double_flag = False
            
            if op.d1_first_pos != 0:
                items.pop(0)                                   


        args['paginator'] = op.get_data_dict()
        args['searchterms'] = searchterms
        args['searchtype'] = searchtype
        args['books'] = items
        args['current'] = 'search'
        # per-user: the list renders this user's shelf/status/rating, so the
        # cached fragment must not be shared between users.
        args['cache_id'] = 'u%s:%s:%s:%s' % (request.user.id, searchterms, searchtype, op.page_num)
        # The list renders this user's mutable state (shelf/status/rating), so it
        # must not be served stale from cache.
        args['cache_t'] = 0
        args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_books.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
@personal_view
def ThemeView(request):
    theme = Theme.objects.filter(user=request.user).first()
    if theme:
        theme.theme_css = "css/sopds-dark.css" if theme.theme_css == "css/sopds.css" else "css/sopds.css"
        theme.save(update_fields=["theme_css"])
    else:
        Theme.objects.create(user=request.user, theme_css="css/sopds-dark.css")
    return HttpResponseRedirect(request.META.get('HTTP_REFERER') or reverse('web:main'))


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
@personal_view
def SettingsView(request):
    prefs = user_prefs(request.user)
    if request.method == 'POST':
        prefs.theme_css = 'css/sopds-dark.css' if request.POST.get('theme') == 'dark' else 'css/sopds.css'
        prefs.reader_mode = (Theme.READER_CHAPTERS if request.POST.get('reader_mode') == Theme.READER_CHAPTERS
                             else Theme.READER_WHOLE)
        try:
            fs = int(request.POST.get('font_size', 100))
        except (TypeError, ValueError):
            fs = 100
        prefs.font_size = min(200, max(70, fs))
        prefs.save()
        return redirect(reverse('web:settings'))

    args = {
        'current': 'settings',
        'breadcrumbs': [_('Settings')],
        'prefs': prefs,
        'is_dark': prefs.theme_css == 'css/sopds-dark.css',
        'css_file': prefs.theme_css,
    }
    args.update(csrf(request))
    return render(request, 'sopds_settings.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
@personal_view
def DeviceSyncView(request):
    """Set the KOReader sync password and show connection details for KOReader
    (kosync) and Moon+ Reader (WebDAV).

    Moon+ Reader's WebDAV uses the normal SOPDS/OIDC login, so nothing is stored
    here for it. KOReader can't use that password (it only sends md5), so the
    user sets a dedicated sync password whose md5 is kept in KosyncCredential.
    """
    from sopds_sync.models import KosyncCredential
    message = None
    if request.method == 'POST':
        if request.POST.get('action') == 'clear':
            KosyncCredential.objects.filter(user=request.user).delete()
            message = _('Sync password removed.')
        else:
            pw = (request.POST.get('sync_password') or '').strip()
            if len(pw) < 6:
                message = _('Sync password must be at least 6 characters long.')
            else:
                cred, created = KosyncCredential.objects.get_or_create(
                    user=request.user, defaults={'auth_key': ''})
                cred.set_password(pw)
                cred.save(update_fields=['auth_key'])
                message = _('Sync password saved.')

    args = {
        'current': 'devicesync',
        'breadcrumbs': [_('Device sync')],
        'message': message,
        'has_cred': KosyncCredential.objects.filter(user=request.user).exists(),
        'kosync_enabled': config.SOPDS_KOSYNC_ENABLE,
        'webdav_enabled': config.SOPDS_WEBDAV_ENABLE,
        'kosync_url': request.build_absolute_uri('/kosync/'),
        'webdav_url': request.build_absolute_uri('/dav/'),
        'css_file': theme_css(request.user),
    }
    args.update(csrf(request))
    return render(request, 'sopds_devicesync.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def SearchSeriesView(request):
    #Read searchtype, searchterms, searchterms0, page from form
    args = {}
    args.update(csrf(request))

    if request.GET:
        searchtype = request.GET.get('searchtype', 'm')
        searchterms = request.GET.get('searchterms', '')
        #searchterms0 = int(request.POST.get('searchterms0', ''))
        page_num = _int_param(request, 'page', 1)
        page_num = page_num if page_num>0 else 1
        
        if searchtype == 'm':
            series = Series.objects.filter(search_ser__contains=searchterms.upper())
        elif searchtype == 'b': 
            series = Series.objects.filter(search_ser__startswith=searchterms.upper())
        elif searchtype == 'e':
            series = Series.objects.filter(search_ser=searchterms.upper())      

        #if len(series)>0:
        #    series = series.order_by('ser')   
        series = series.annotate(count_book=Count('book')).distinct().order_by('search_ser') 
            
        # Создаем результирующее множество
        series_count = series.count()
        op = OPDS_Paginator(series_count, 0, page_num, config.SOPDS_MAXITEMS, HALF_PAGES_LINKS)        
        items = []
        if searchtype == 'm' and searchterms:
            page_series = contains_page(Series.objects.annotate(count_book=Count('book')),
                                        'opds_catalog_series', 'search_ser', searchterms.upper(),
                                        'search_ser', 'search_ser',
                                        op.d1_last_pos - op.d1_first_pos + 1, op.d1_first_pos)
        else:
            page_series = series[op.d1_first_pos:op.d1_last_pos+1]
        for row in page_series:
            #p = {'id':row.id, 'ser':row.ser, 'lang_code': row.lang_code, 'book_count': Book.objects.filter(series=row).count()}
            p = {'id':row.id, 'ser':row.ser, 'lang_code': row.lang_code, 'book_count': row.count_book}
            items.append(p)                     
              
        args['paginator'] = op.get_data_dict()
        args['searchterms']=searchterms
        args['searchtype']=searchtype
        args['series']=items     
        args['searchobject'] = 'series'
        args['current'] = 'search'        
        args['breadcrumbs'] = [_('Series'),_('Search'),searchterms]
        args['cache_id']='%s:%s:%s'%(searchterms,searchtype,op.page_num)
        args['cache_t']=config.SOPDS_CACHE_TIME
        args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_series.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def SearchAuthorsView(request):
    #Read searchtype, searchterms, searchterms0, page from form    
    args = {}
    args.update(csrf(request))

    if request.GET:
        searchtype = request.GET.get('searchtype', 'm')
        searchterms = request.GET.get('searchterms', '')
        #searchterms0 = int(request.POST.get('searchterms0', ''))
        page_num = _int_param(request, 'page', 1)
        page_num = page_num if page_num>0 else 1
        
        if searchtype == 'm':
            authors = Author.objects.filter(search_full_name__contains=searchterms.upper()).order_by('search_full_name')   
        elif searchtype == 'b':
            authors = Author.objects.filter(search_full_name__startswith=searchterms.upper()).order_by('search_full_name')    
        elif searchtype == 'e': 
            authors = Author.objects.filter(search_full_name=searchterms.upper()).order_by('search_full_name')    
                        
        # Создаем результирующее множество
        authors_count = authors.count()
        op = OPDS_Paginator(authors_count, 0, page_num, config.SOPDS_MAXITEMS, HALF_PAGES_LINKS)        
        items = []
        
        if searchtype == 'm' and searchterms:
            page_authors = contains_page(Author.objects, 'opds_catalog_author', 'search_full_name',
                                         searchterms.upper(), 'search_full_name', 'search_full_name',
                                         op.d1_last_pos - op.d1_first_pos + 1, op.d1_first_pos)
        else:
            page_authors = authors[op.d1_first_pos:op.d1_last_pos+1]
        for row in page_authors:
            p = {'id':row.id, 'full_name':row.full_name, 'lang_code': row.lang_code, 'book_count': Book.objects.filter(authors=row).count()}
            items.append(p)                     
            
        args['paginator'] = op.get_data_dict()              
        args['searchterms'] = searchterms
        args['searchtype'] = searchtype
        args['authors'] = items
        args['searchobject'] = 'author'
        args['current'] = 'search'       
        args['breadcrumbs'] = [_('Authors'), _('Search'),searchterms]
        args['cache_id'] = '%s:%s:%s' % (searchterms, searchtype, op.page_num)
        args['cache_t'] = config.SOPDS_CACHE_TIME
        args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_authors.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def SearchGenresView(request):
    """Find genres by name.

    Genres were browsable through the section tree but not searchable, so
    reaching a leaf like "Detective" meant knowing which section it sits under.
    Matching is on `subsection` — the leaf name a reader would type — with
    `section` shown alongside for context.
    """
    args = {}
    args.update(csrf(request))

    if request.GET:
        searchtype = request.GET.get('searchtype', 'm')
        searchterms = (request.GET.get('searchterms') or '').strip()
        page_num = _int_param(request, 'page', 1)
        page_num = page_num if page_num > 0 else 1

        if searchtype == 'b':
            genres = Genre.objects.filter(subsection__istartswith=searchterms)
        elif searchtype == 'e':
            genres = Genre.objects.filter(subsection__iexact=searchterms)
        else:
            genres = Genre.objects.filter(subsection__icontains=searchterms)

        genres = (genres.annotate(num_book=Count('book'))
                  .filter(num_book__gt=0).order_by('section', 'subsection'))

        op = OPDS_Paginator(genres.count(), 0, page_num,
                            config.SOPDS_MAXITEMS, HALF_PAGES_LINKS)
        args['items'] = [
            {'id': row.id, 'section': row.section, 'subsection': row.subsection,
             'num_book': row.num_book}
            for row in genres[op.d1_first_pos:op.d1_last_pos + 1]]

        args['paginator'] = op.get_data_dict()
        args['searchterms'] = searchterms
        args['searchtype'] = searchtype
        args['searchobject'] = 'genre'
        args['current'] = 'search'
        args['is_search'] = True
        # Truthy so the shared template links straight to the books in a genre
        # rather than drilling into a section.
        args['parent_id'] = -1
        args['breadcrumbs'] = [_('Genres'), _('Search'), searchterms]
        args['cache_id'] = 'g:%s:%s:%s' % (searchterms, searchtype, op.page_num)
        args['cache_t'] = config.SOPDS_CACHE_TIME
        args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_selectgenres.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def CatalogsView(request):   
    args = {}

    if request.GET:
        cat_id = request.GET.get('cat', None)
        page_num = _int_param(request, 'page', 1)   
    else:
        cat_id = None
        page_num = 1

    try:
        if cat_id is not None:
            cat = Catalog.objects.get(id=cat_id)
        else:
            cat = Catalog.objects.get(parent__id=cat_id)
    except Catalog.DoesNotExist:
        cat = None
    
    catalogs_list = Catalog.objects.filter(parent=cat).order_by("cat_name")
    catalogs_count = catalogs_list.count()
    # prefetch_related on sqlite on items >999 therow error "too many SQL variables"
    #books_list = Book.objects.filter(catalog=cat).prefetch_related('authors','genres','series').order_by("title")
    books_list = Book.objects.filter(catalog=cat).order_by("search_title")
    books_count = books_list.count()
    
    # Получаем результирующий список
    op = OPDS_Paginator(catalogs_count, books_count, page_num, config.SOPDS_MAXITEMS, HALF_PAGES_LINKS)
    items = []
    
    for row in catalogs_list[op.d1_first_pos:op.d1_last_pos+1]:
        p = {'is_catalog':1, 'title': row.cat_name,'id': row.id, 'cat_type':row.cat_type, 'parent_id':row.parent_id}
        items.append(p)

    # Prefetch related rows for the page instead of ~5 queries per book.
    prefetch = ['authors', 'genres', 'series', 'bseries_set']
    if config.SOPDS_AUTH:
        prefetch.append(Prefetch('bookshelf_set',
                                 queryset=bookshelf.objects.filter(user=request.user),
                                 to_attr='user_shelf'))
    page_books = books_list.prefetch_related(*prefetch)

    for row in page_books[op.d2_first_pos:op.d2_last_pos+1]:
        user_shelf = getattr(row, 'user_shelf', []) if config.SOPDS_AUTH else []
        p = {'is_catalog':0, 'lang_code': row.lang_code, 'filename': row.filename, 'path': row.path, \
              'registerdate': row.registerdate, 'id': row.id, 'annotation': strip_tags(row.annotation), \
              'docdate': row.docdate, 'format': row.format, 'title': row.title, 'filesize': row.filesize//1000,\
              'authors':row.authors.all(), 'genres':row.genres.all(), 'series':row.series.all(), 'ser_no':row.bseries_set.all(),\
              'readtime': user_shelf if config.SOPDS_AUTH else None,
              'status': user_shelf[0].status if user_shelf else '',
              'rating': user_shelf[0].rating if user_shelf else None,
              'percent': user_shelf[0].percent if user_shelf else None
             }
        items.append(p)
                    
    args['paginator'] = op.get_data_dict()
    args['items']=items
    args['cat_id'] = cat_id
    args['current'] = 'catalog'     
    
    breadcrumbs_list = []
    if cat:
        while (cat.parent):
            breadcrumbs_list.insert(0, (cat.cat_name, cat.id))
            cat = cat.parent
        breadcrumbs_list.insert(0, (_('ROOT'), 0))  
    #breadcrumbs_list.insert(0, (_('Catalogs'),-1))    
    args['breadcrumbs_cat'] =  breadcrumbs_list  
    args['breadcrumbs'] =  [_('Catalogs')]
    args['cache_id'] = 'u%s:%s:%s:%s' % (request.user.id, args['current'], cat_id, op.page_num)
    # per-user mutable state (shelf/status/rating) is rendered here; don't cache stale
    args['cache_t'] = 0
    args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_catalogs.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def BooksView(request):   
    args = {}

    if request.GET:
        lang_code = _int_param(request, 'lang', 0)  
        chars = request.GET.get('chars', '')
    else:
        lang_code = 0
        chars = ''
        
    items = alphabet_menu('opds_catalog_book', 'search_title', lang_code, chars.upper())

    args['items']=items
    args['current'] = 'book'
    args['lang_code'] = lang_code   
    args['breadcrumbs'] =  [_('Books'),_('Select'),lang_menu[lang_code],chars]
    args['cache_id'] = '%s:%s:%s' % (args['current'],lang_code, chars)
    args['cache_t'] = config.SOPDS_CACHE_TIME
    args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_selectbook.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def AuthorsView(request):   
    args = {}

    if request.GET:
        lang_code = _int_param(request, 'lang', 0)  
        chars = request.GET.get('chars', '')
    else:
        lang_code = 0
        chars = ''
        
    items = alphabet_menu('opds_catalog_author', 'search_full_name', lang_code, chars.upper())

    args['items']=items
    args['current'] = 'author'
    args['lang_code'] = lang_code   
    args['breadcrumbs'] =  [_('Authors'),_('Select'),lang_menu[lang_code],chars]
    args['cache_id'] = '%s:%s:%s' % (args['current'],lang_code, chars)
    args['cache_t'] = config.SOPDS_CACHE_TIME
    args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_selectauthor.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def SeriesView(request):   
    args = {}

    if request.GET:
        lang_code = _int_param(request, 'lang', 0)  
        chars = request.GET.get('chars', '')
    else:
        lang_code = 0
        chars = ''
        
    items = alphabet_menu('opds_catalog_series', 'search_ser', lang_code, chars.upper())

    args['items']=items
    args['current'] = 'series'
    args['lang_code'] = lang_code   
    args['breadcrumbs'] =  [_('Series'),_('Select'),lang_menu[lang_code],chars]
    args['cache_id'] = '%s:%s:%s' % (args['current'],lang_code, chars)
    args['cache_t'] = config.SOPDS_CACHE_TIME
    args['css_file'] = theme_css(request.user)

    return render(request,'sopds_selectseries.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def GenresView(request):   
    args = {}

    if request.GET:
        try:
            section_id = int(request.GET.get('section', '0'))
        except (TypeError, ValueError):
            raise Http404
    else:
        section_id = 0
        
    if section_id==0:
        items = Genre.objects.values('section').annotate(section_id=Min('id'), num_book=Count('book')).filter(num_book__gt=0).order_by('section')
        args['breadcrumbs'] =  [_('Genres'),_('Select')]
    else:
        section = get_object_or_404(Genre, id=section_id).section
        items = Genre.objects.filter(section=section).annotate(num_book=Count('book')).filter(num_book__gt=0).values().order_by('subsection')   
        args['breadcrumbs'] =  [_('Genres'),_('Select'),section]   
          
    args['items']=items
    args['current'] = 'genre'  
    args['parent_id'] = section_id
    args['cache_id'] = '%s:%s' % (args['current'],section_id)
    args['cache_t'] = config.SOPDS_CACHE_TIME
    args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_selectgenres.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
@personal_view
def BSAddView(request):
    book = request.GET.get('book')
    if book:
        try:
            bookshelf.objects.get_or_create(user=request.user, book_id=int(book))
        except (TypeError, ValueError):
            book = None

    # Fall back to the main page when there is no Referer (previously
    # None.split(...) -> 500), and don't assume the `book` param is present.
    referer = request.META.get('HTTP_REFERER')
    if referer:
        target = referer.split('#')[0]
        if book:
            target = '%s#%s' % (target, book)
    else:
        target = reverse('web:main')
    return HttpResponseRedirect(target)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
@personal_view
def BSDelView(request):
    book = request.GET.get('book') or request.POST.get('book')
    if book:
        try:
            bookshelf.objects.filter(user=request.user, book=int(book)).delete()
        except (TypeError, ValueError):
            pass

    target = request.META.get('HTTP_REFERER') or reverse('web:main')
    # For htmx (hx-delete), ask the client to reload via HX-Redirect instead of
    # returning a normal redirect it would try to swap into the page.
    if request.headers.get('HX-Request'):
        response = HttpResponse(status=204)
        response['HX-Redirect'] = target
        return response
    return HttpResponseRedirect(target)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
@personal_view
def BSSetPos(request, book_id):
    pos = request.GET.get('pos') if request.GET else None

    # `pos` is a paragraph-div id like "2.13" (see FB2_22_xhtml.xsl). Store it
    # verbatim as text; validate to only accept that shape. update_or_create so
    # the position is remembered even if the book is not on the shelf yet.
    if pos and len(pos) <= 32 and all(c in '0123456789.' for c in pos):
        bookshelf.objects.update_or_create(
            user=request.user, book_id=book_id, defaults={'position': pos})

    response = HttpResponse()
    response.write('OK')

    return response


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
@personal_view
def BSGetPos(request, book_id):
    shelf = bookshelf.objects.filter(user=request.user, book_id=book_id).first()
    response = HttpResponse()
    response.write(shelf.position if shelf and shelf.position else '')
    return response


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
@personal_view
def BSClearView(request):
    bookshelf.objects.filter(user=request.user).delete()
    return redirect("%s?searchtype=u" % reverse("web:searchbooks"))


@sopds_login(url='web:login')
@personal_view
def BSSetStatus(request, book_id):
    status = request.POST.get('status', '')
    valid = {c[0] for c in bookshelf.STATUS_CHOICES}
    if status not in valid:
        return JsonResponse({'ok': False})
    obj, _ = bookshelf.objects.get_or_create(user=request.user, book_id=book_id)
    obj.status = status
    obj.save(update_fields=['status'])
    return JsonResponse({'ok': True, 'status': status})


@sopds_login(url='web:login')
@personal_view
def BSSetRating(request, book_id):
    try:
        rating = int(request.POST.get('rating'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False})
    if rating < 0 or rating > 5:
        return JsonResponse({'ok': False})
    obj, _ = bookshelf.objects.get_or_create(user=request.user, book_id=book_id)
    obj.rating = rating or None   # 0 clears the rating
    obj.save(update_fields=['rating'])
    return JsonResponse({'ok': True, 'rating': obj.rating})


def hello(request):
    args = {}
    args['breadcrumbs'] = [_('HOME')]
    if request.user.is_authenticated:
        args['css_file'] = theme_css(request.user)
    else:
        args['css_file'] = "css/sopds.css"
    return render(request, 'sopds_hello.html', args)


@sopds_login(url='web:login')
def SearchSuggestView(request):
    """Live search suggestions for the search box (htmx endpoint).

    Returns an HTML fragment with up to 10 matches for the current query,
    scoped to the selected search type (title/author/series). Each suggestion
    links to the corresponding search result. Behind @sopds_login so it honours
    SOPDS_AUTH like the rest of the UI.
    """
    term = (request.POST.get('searchterms') or request.GET.get('searchterms') or '').strip()
    suggesttype = request.POST.get('suggesttype') or request.GET.get('suggesttype') or 'title'
    suggestions = []
    # Require >=3 chars: the pg_trgm index needs a full trigram, and 2-char
    # substrings match too much to be useful. No ORDER BY here on purpose — an
    # `ORDER BY search_title` makes PostgreSQL drive the query off the plain
    # btree index and filter LIKE '%..%' row-by-row (slow on large, cyrillic-
    # heavy catalogs) instead of using the trigram index. Autocomplete does not
    # need sorted output.
    if len(term) >= 3:
        up = term.upper()
        base = reverse('web:searchbooks')
        if suggesttype == 'author':
            for a in Author.objects.filter(search_full_name__contains=up)[:10]:
                suggestions.append({'label': a.full_name, 'url': '%s?searchtype=a&searchterms=%d' % (base, a.id)})
        elif suggesttype == 'series':
            for s in Series.objects.filter(search_ser__contains=up)[:10]:
                suggestions.append({'label': s.ser, 'url': '%s?searchtype=s&searchterms=%d' % (base, s.id)})
        else:
            # Show the first author next to the title so books with the same or
            # similar title are distinguishable. prefetch_related avoids an extra
            # query per suggestion.
            for b in Book.objects.filter(search_title__contains=up).prefetch_related('authors')[:10]:
                authors = list(b.authors.all())
                label = '%s — %s' % (b.title, authors[0].full_name) if authors else b.title
                suggestions.append({'label': label, 'url': '%s?searchtype=i&searchterms=%d' % (base, b.id)})
    return render(request, 'sopds_search_suggestions.html', {'suggestions': suggestions})


def OIDCLoginView(request):
    """Start the OIDC (Keycloak) login flow (Authlib handles state/nonce/PKCE)."""
    if not oidc.oidc_enabled():
        raise Http404
    next_url = request.GET.get('next', reverse('web:main'))
    if not url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}):
        next_url = reverse('web:main')
    request.session['oidc_next'] = next_url
    redirect_uri = request.build_absolute_uri(reverse('web:oidc_callback'))
    return oidc.get_client().authorize_redirect(request, redirect_uri)


def OIDCCallbackView(request):
    """OIDC redirect target: exchange the code, provision the user, log in."""
    if not oidc.oidc_enabled():
        raise Http404
    client = oidc.get_client()
    try:
        token = client.authorize_access_token(request)
    except Exception:
        args = {'breadcrumbs': [_('Login')], 'css_file': 'css/sopds.css',
                'system_message': {'text': _('OIDC authentication failed.'), 'type': 'alert'}}
        return handler403(request, args)

    userinfo = token.get('userinfo') or client.userinfo(token=token)
    user = oidc.provision_user(userinfo)
    if user is None:
        args = {'breadcrumbs': [_('Login')], 'css_file': 'css/sopds.css',
                'system_message': {'text': _('This account cannot sign in via OIDC.'), 'type': 'alert'}}
        return handler403(request, args)

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    next_url = request.session.pop('oidc_next', None) or reverse('web:main')
    return redirect(next_url)


# Simple cache-backed brute-force throttle for the web login form: at most
# LOGIN_RATE_LIMIT failed attempts per client IP within (and locked out for)
# LOGIN_RATE_WINDOW seconds. Uses the shared cache (Redis in production), so it
# holds across uWSGI workers. OPDS Basic-auth is intentionally not throttled —
# e-readers legitimately re-send credentials on every request.
LOGIN_RATE_LIMIT = 10
LOGIN_RATE_WINDOW = 300


def _login_ratelimit_key(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    ip = xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', '')
    return 'ratelimit:login:%s' % ip


def _login_is_blocked(request):
    return cache.get(_login_ratelimit_key(request), 0) >= LOGIN_RATE_LIMIT


def _login_register_failure(request):
    key = _login_ratelimit_key(request)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, LOGIN_RATE_WINDOW)


def _login_clear(request):
    cache.delete(_login_ratelimit_key(request))


def LoginView(request):
    args = {}
    args['breadcrumbs'] = [_('Login')]
    args['css_file'] = "css/sopds.css"
    args.update(csrf(request))
    try:
        username = request.POST['username']
        password = request.POST['password']
    except KeyError:
        return render(request, 'sopds_login.html', args)

    if _login_is_blocked(request):
        args['system_message'] = {'text': _('Too many login attempts. Please try again later.'), 'type': 'alert'}
        return handler403(request, args)

    next_url = request.GET.get('next',reverse("web:main"))
    # Reject off-site ?next= targets to prevent an open redirect after login.
    if not url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("web:main")

    user = authenticate(username=username, password=password)
    if user is not None:
        if user.is_active:
            _login_clear(request)
            login(request, user)
            return redirect(next_url)
        else:
            _login_register_failure(request)
            args['system_message']={'text':_('This account is not active!'),'type':'alert'}
            return handler403(request,args)
    else:
        _login_register_failure(request)
        args['system_message']={'text':_('User does not exist or the password is incorrect!'),'type':'alert'}
        return handler403(request,args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def LogoutView(request):
    logout(request)
    args = {}
    args['breadcrumbs'] = [_('Logout')]
    return redirect(reverse('web:main'))


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def BookReaderView(request, book_id):
    # The page is only a shell: it fetches opds:read, which renders FB2 and EPUB
    # and nothing else. Refuse here as well, so an unreadable format fails as a
    # 404 on the link instead of loading a reader that stays permanently empty.
    book = get_object_or_404(Book, id=book_id)
    if book.format not in dl.READABLE_FORMATS:
        raise Http404

    # Counted here rather than on the content route: that one answers a
    # revalidation with 304 before the view runs, so re-opening a book would go
    # unrecorded. Opening the reader is the event worth counting anyway.
    stats.record(book.id, stats.READS)

    prefs = user_prefs(request.user)
    args = {}
    args['current'] = 'reader'
    args['book_id'] = book_id
    args['reader_mode'] = prefs.reader_mode
    args['font_size'] = prefs.font_size
    args['css_file'] = prefs.theme_css
    return render(request, 'BookReader.html', args)


def handler403(request, args):
    response = render(request, 'sopds_login.html', args)
    response.status_code = 403
    return response
