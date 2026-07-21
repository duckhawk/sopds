from random import randint

from django.shortcuts import render, redirect
from django.template.context_processors import csrf
from django.core.cache import cache
from django.db.models import Count, Min, Max, Prefetch
from django.utils.translation import gettext as _
from django.contrib.auth import authenticate, login, logout, REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import user_passes_test
from django.views.decorators.vary import vary_on_headers
from django.urls import reverse, reverse_lazy
from django.utils.html import strip_tags
from django.db.models import Q
from django.http import HttpResponseForbidden, HttpResponseRedirect


from opds_catalog import models
from opds_catalog.models import Book, Author, Series, bookshelf, Counter, Catalog, Genre, lang_menu, Theme
from opds_catalog import settings
from opds_catalog.utils import alphabet_menu
from constance import config
from opds_catalog.opds_paginator import Paginator as OPDS_Paginator


from sopds_web_backend.settings import HALF_PAGES_LINKS
from django.http import HttpResponse, JsonResponse


def theme_css(user):
    """Return the user's theme css in a single query (instead of exists()+get())."""
    theme = Theme.objects.filter(user=user).first()
    return theme.theme_css if theme else "css/sopds.css"


def user_prefs(user):
    """Per-user preferences row (theme + reader settings), created on demand."""
    prefs, _ = Theme.objects.get_or_create(user=user)
    return prefs


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
        page_num = int(request.GET.get('page', '1'))
        page_num = page_num if page_num>0 else 1
        
        #if (len(searchterms)<3) and (searchtype in ('m', 'b', 'e')):
        #    args['errormsg'] = 'Too few symbols in search string !';
        #    return render_to_response('sopds_error.html', args)
        
        if searchtype == 'm':
            #books = Book.objects.extra(where=["upper(title) like %s"], params=["%%%s%%"%searchterms.upper()]).order_by('title','-docdate')
            books = Book.objects.filter(search_title__contains=searchterms.upper()).order_by('search_title','-docdate')
            args['breadcrumbs'] = [_('Books'),_('Search by title'),searchterms]
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
                                   
        # Поиск книг на книжной полке            
        elif searchtype == 'u':
            if config.SOPDS_AUTH:
                books = Book.objects.filter(bookshelf__user=request.user).order_by('-bookshelf__readtime')
                args['breadcrumbs'] = [_('Books'),_('Bookshelf'),request.user.username]
                #books = bookshelf.objects.filter(user=request.user).select_related('book')              
            else:
                books = Book.objects.filter(id=0)
                args['breadcrumbs'] = [_('Books'), _('Bookshelf')] 
            args['searchobject'] = 'title'
            args['isbookshelf'] = 1
                
        # Поиск дубликатов для книги            
        elif searchtype == 'd':
            #try:
            book_id = int(searchterms)
            mbook = Book.objects.get(id=book_id)
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
            args['breadcrumbs'] = [_('Books'),books[0].title]
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
        page_books = books.prefetch_related(*prefetch)

        for row in page_books[start:finish+1]:
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
                 'filesize': row.filesize // 1000,
                 'authors': row.authors.all(),
                 'genres': row.genres.all(),
                 'series': row.series.all(),
                 'ser_no': row.bseries_set.all(),
                 'bookshelf': bool(user_shelf),
                 'readtime': user_shelf if config.SOPDS_AUTH else None,
                 'status': user_shelf[0].status if user_shelf else '',
                 'rating': user_shelf[0].rating if user_shelf else None
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
        # changes on bookshelf should be refreshed immediatelly
        if searchtype == 'u':
            args['cache_t'] = 0
        else:
            args['cache_t'] = config.SOPDS_CACHE_TIME
        args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_books.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def ThemeView(request):
    theme = Theme.objects.filter(user=request.user).first()
    if theme:
        theme.theme_css = "css/sopds-dark.css" if theme.theme_css == "css/sopds.css" else "css/sopds.css"
        theme.save(update_fields=["theme_css"])
    else:
        Theme.objects.create(user=request.user, theme_css="css/sopds-dark.css")
    return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
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
def SearchSeriesView(request):
    #Read searchtype, searchterms, searchterms0, page from form
    args = {}
    args.update(csrf(request))

    if request.GET:
        searchtype = request.GET.get('searchtype', 'm')
        searchterms = request.GET.get('searchterms', '')
        #searchterms0 = int(request.POST.get('searchterms0', ''))
        page_num = int(request.GET.get('page', '1'))
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
        for row in series[op.d1_first_pos:op.d1_last_pos+1]:
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
        page_num = int(request.GET.get('page', '1'))
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
        
        for row in authors[op.d1_first_pos:op.d1_last_pos+1]:
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
def CatalogsView(request):   
    args = {}

    if request.GET:
        cat_id = request.GET.get('cat', None)
        page_num = int(request.GET.get('page', '1'))   
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
              'rating': user_shelf[0].rating if user_shelf else None
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
    args['cache_t'] = config.SOPDS_CACHE_TIME
    args['css_file'] = theme_css(request.user)

    return render(request, 'sopds_catalogs.html', args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def BooksView(request):   
    args = {}

    if request.GET:
        lang_code = int(request.GET.get('lang', '0'))  
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
        lang_code = int(request.GET.get('lang', '0'))  
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
        lang_code = int(request.GET.get('lang', '0'))  
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
        section_id = int(request.GET.get('section', '0'))  
    else:
        section_id = 0
        
    if section_id==0:
        items = Genre.objects.values('section').annotate(section_id=Min('id'), num_book=Count('book')).filter(num_book__gt=0).order_by('section')
        args['breadcrumbs'] =  [_('Genres'),_('Select')]
    else:
        section = Genre.objects.get(id=section_id).section
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
def BSAddView(request):
    if request.GET and request.GET.get('book', None):
        book = int(request.GET['book'])
        bookshelf.objects.get_or_create(user=request.user,
                                        book_id=book)

    return HttpResponseRedirect(request.META.get('HTTP_REFERER').split('#')[0] + '#' + request.GET['book'])


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def BSDelView(request):
    if request.GET and request.GET.get('book', None):
        book = request.GET.get('book', None)
        bookshelf.objects.filter(user=request.user, book=int(book)).delete()

    return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
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
def BSGetPos(request, book_id):
    shelf = bookshelf.objects.filter(user=request.user, book_id=book_id).first()
    response = HttpResponse()
    response.write(shelf.position if shelf and shelf.position else '')
    return response


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url='web:login')
def BSClearView(request):
    bookshelf.objects.filter(user=request.user).delete()
    return redirect("%s?searchtype=u" % reverse("web:searchbooks"))


@sopds_login(url='web:login')
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
    
    next_url = request.GET.get('next',reverse("web:main"))

    user = authenticate(username=username, password=password)
    if user is not None:
        if user.is_active:
            login(request, user)
            return redirect(next_url)
        else:
            args['system_message']={'text':_('This account is not active!'),'type':'alert'}
            return handler403(request,args)
            #return render(request, 'sopds_login.html', args)
    else:
        args['system_message']={'text':_('User does not exist or the password is incorrect!'),'type':'alert'}
        return handler403(request,args)
        #return render(request, 'sopds_login.html', args)

    return handler403(request,args)
    #return render(request, 'sopds_login.html', args)


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
