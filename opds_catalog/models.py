from django.db import models
from django.db.models import F
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _lazy

counter_allbooks = 'allbooks'
counter_allcatalogs = 'allcatalogs'
counter_allauthors = 'allauthors'
counter_allgenres = 'allgenres'
counter_allseries = 'allseries'

SIZE_BOOK_FILENAME   = 512
SIZE_BOOK_PATH       = 512
SIZE_BOOK_FORMAT     = 8
SIZE_BOOK_DOCDATE    = 32
SIZE_BOOK_LANG       = 16
SIZE_BOOK_TITLE      = 512
SIZE_BOOK_ANNOTATION = 10000
SIZE_BOOK_ISBN       = 20
SIZE_BOOK_PUBLISHER  = 128

SIZE_CAT_CATNAME     = 190
SIZE_CAT_PATH        = SIZE_BOOK_PATH

SIZE_AUTHOR_NAME     = 128

SIZE_GENRE           = 32
SIZE_GENRE_SECTION   = 64
SIZE_GENRE_SUBSECTION = 100

SIZE_SERIES          = 150


LangCodes = {1:'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЬЫЪЭЮЯабвгдеёжзийклмнопрстуфхцчшщьыъэюя',
             2:'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz',
             3:'0123456789'}
lang_menu = {1:_lazy('Cyrillic'), 2:_lazy('Latin'), 3:_lazy('Digits'), 9:_lazy('Other symbols'), 0:_lazy('Show all')}


class Book(models.Model):
    filename = models.CharField(max_length=SIZE_BOOK_FILENAME,db_index=True)
    path = models.CharField(max_length=SIZE_BOOK_PATH,db_index=True)
    filesize = models.IntegerField(null=False, default=0)
    format = models.CharField(max_length=SIZE_BOOK_FORMAT)
    catalog = models.ForeignKey('Catalog',db_index=True, on_delete=models.CASCADE)
    cat_type = models.IntegerField(null=False, default=0)
    registerdate = models.DateTimeField(null=False, default=timezone.now)
    docdate = models.CharField(max_length=SIZE_BOOK_DOCDATE,db_index=True)
    #favorite = models.IntegerField(null=False, default=0)
    lang = models.CharField(max_length=SIZE_BOOK_LANG)
    title = models.CharField(max_length=SIZE_BOOK_TITLE, db_index=True)
    search_title = models.CharField(max_length=SIZE_BOOK_TITLE, default='', db_index=True)
    annotation = models.CharField(max_length=SIZE_BOOK_ANNOTATION)
    isbn = models.CharField(max_length=SIZE_BOOK_ISBN, default='', blank=True, db_index=True)
    # Filled by `sopds_enrich` from Open Library, not by the scanner: FB2/EPUB
    # metadata rarely carries a usable publisher, and no parser reads one.
    publisher = models.CharField(max_length=SIZE_BOOK_PUBLISHER, default='', blank=True)
    # When the last successful Open Library lookup ran, so re-runs skip books
    # already tried instead of asking the API about them again.
    enriched = models.DateTimeField(null=True, default=None, db_index=True)
    lang_code = models.IntegerField(null=False, default=9, db_index=True)
    avail = models.IntegerField(null=False, default=0, db_index=True)
    authors = models.ManyToManyField('Author', through='bauthor')
    genres = models.ManyToManyField('Genre', through='bgenre')
    series = models.ManyToManyField('Series', through='bseries')

    class Meta:
        indexes = [
            # Serves the "recently added" feed and page, which page through the
            # whole catalogue ordered by registration date. Composite and
            # descending so the ordering (including the id tiebreaker that keeps
            # pagination stable for books registered in the same scan) is read
            # straight off the index instead of sorting the table.
            models.Index(F('registerdate').desc(), F('id').desc(),
                         name='book_registerdate_desc'),
        ]


class Catalog(models.Model):
    parent = models.ForeignKey('self', null=True, db_index=True, on_delete=models.CASCADE)
    cat_name = models.CharField(max_length=SIZE_CAT_CATNAME, db_index=True)
    path = models.CharField(max_length=SIZE_CAT_PATH, db_index=True)
    cat_type = models.IntegerField(null=False, default=0)
    cat_size = models.BigIntegerField(null=True, default=0)


class Author(models.Model):
    full_name = models.CharField(max_length=SIZE_AUTHOR_NAME, default='', db_index=True)
    search_full_name = models.CharField(max_length=SIZE_AUTHOR_NAME, default='', db_index=True)
    lang_code = models.IntegerField(null=False, default=9, db_index=True)


class bauthor(models.Model):
    book = models.ForeignKey('Book', db_index=True, on_delete=models.CASCADE)
    author = models.ForeignKey('Author', db_index=True, on_delete=models.CASCADE)
#    class Meta:
#        index_together = [
#            ["book", "author"],
#        ]


class Genre(models.Model):
    genre = models.CharField(max_length=SIZE_GENRE, db_index=True)
    section = models.CharField(max_length=SIZE_GENRE_SECTION, db_index=True)
    subsection = models.CharField(max_length=SIZE_GENRE_SUBSECTION, db_index=True)

class bgenre(models.Model):
    book = models.ForeignKey('Book', db_index=True, on_delete=models.CASCADE)
    genre = models.ForeignKey('Genre', db_index=True, on_delete=models.CASCADE)


class Series(models.Model):
    ser = models.CharField(max_length=SIZE_SERIES, db_index=True)
    search_ser = models.CharField(max_length=SIZE_SERIES, default='', db_index=True)
    lang_code = models.IntegerField(null=False, default=9,db_index=True)


class bseries(models.Model):
    book = models.ForeignKey('Book', db_index=True, on_delete=models.CASCADE)
    ser = models.ForeignKey('Series', db_index=True, on_delete=models.CASCADE)
    ser_no = models.IntegerField(null=False, default=0)
#    class Meta:
#        index_together = [
#            ["book", "ser"],
#        ]


class Theme(models.Model):
    # Per-user preferences (theme + reader settings), edited on /web/settings/.
    READER_WHOLE = 'whole'
    READER_CHAPTERS = 'chapters'
    READER_MODE_CHOICES = [(READER_WHOLE, 'Whole text'), (READER_CHAPTERS, 'By chapters')]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    theme_css = models.CharField(max_length=64, default='css/sopds.css')
    reader_mode = models.CharField(max_length=16, choices=READER_MODE_CHOICES, default=READER_WHOLE)
    font_size = models.PositiveSmallIntegerField(default=100)  # percent, 70..200


class bookshelf(models.Model):
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    book = models.ForeignKey(Book, db_index=True, on_delete=models.CASCADE)
    readtime = models.DateTimeField(null=False, default=timezone.now, db_index=True)
    # Reading position is the id of a paragraph <div> in the rendered book, of
    # the form "<section>.<paragraph>" (e.g. "2.13"). It must be stored as text:
    # storing it as a float collapsed ids like "1.10" and "1.1" to the same
    # value and dropped trailing zeros, so the saved position never matched.
    position = models.CharField(max_length=32, null=True, default=None)
    # Per-user reading status and rating for the book.
    STATUS_TO_READ = 'to_read'
    STATUS_READING = 'reading'
    STATUS_READ = 'read'
    STATUS_CHOICES = [('', '—'), (STATUS_TO_READ, 'To read'), (STATUS_READING, 'Reading'), (STATUS_READ, 'Read')]
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='', blank=True)
    rating = models.PositiveSmallIntegerField(null=True, default=None)  # 1..5
    # How far through the book the reader is, 0.0..1.0. Unlike `position`, which
    # is a paragraph id only the in-browser reader understands, this is a format
    # the e-reader protocols also speak, so kosync progress can land here.
    percent = models.FloatField(null=True, default=None)

    class Meta:
        unique_together = ['user', 'book']

class CounterManager(models.Manager):
    def update(self, counter_name, counter_value):
        self.update_or_create(name=counter_name, defaults = {"value":counter_value, "update_time":timezone.now()})

    def update_known_counters(self):
        self.update(counter_allbooks, Book.objects.all().count())
        self.update(counter_allcatalogs, Catalog.objects.all().count())
        self.update(counter_allauthors, Author.objects.all().count())
        self.update(counter_allgenres, Genre.objects.all().count())
        self.update(counter_allseries, Series.objects.all().count())


    def get_counter(self, counter_name):
        try:
            counter = self.get(name=counter_name).value
        except ObjectDoesNotExist:
            counter = 0
            
        return counter

    def get_lastscan(self):
        try:
            lastscan = self.get(name='allbooks').update_time
        except ObjectDoesNotExist:
            lastscan = None

        return lastscan


class Counter(models.Model):
    name = models.CharField(primary_key=True, null=False, blank=False, max_length=16)
    value = models.IntegerField(null=False, default=0)
    update_time = models.DateTimeField(null=False, default=timezone.now)
    obj = models.Manager()
    objects = CounterManager()


class BookStat(models.Model):
    """How often a book has been taken out of the library.

    Aggregate counters, deliberately not a log of events. A per-request table
    would grow without bound under e-reader polling and would amount to a
    reading history for every user, which is a much bigger thing to store than
    "how popular is this book" — and popularity is all anything here needs. The
    numbers are anonymous: nothing records *who* downloaded what. A user's own
    history already lives in `bookshelf`, where they can see and clear it.
    """
    book = models.OneToOneField(Book, on_delete=models.CASCADE,
                                primary_key=True, related_name='stat')
    downloads = models.PositiveIntegerField(default=0)
    reads = models.PositiveIntegerField(default=0)
    last_used = models.DateTimeField(null=True, default=None)

    class Meta:
        indexes = [
            # Serves the "most downloaded" listing, which pages through the
            # whole table in descending order.
            models.Index(F('downloads').desc(), name='bookstat_downloads_desc'),
        ]

    def __str__(self):
        return 'book %s: %d downloads, %d reads' % (self.book_id, self.downloads, self.reads)


class ScanRun(models.Model):
    """What one library scan did.

    The scanner counted all of this already and then only wrote it to a log
    file, so the outcome of a scan was invisible to the application: how long
    it took, how much it added, how many files it could not parse — and, worst,
    whether it finished at all. A run that dies leaves a row saying so instead
    of simply never updating anything.
    """
    RUNNING = 'running'
    OK = 'ok'
    FAILED = 'failed'
    STATUS_CHOICES = [(RUNNING, 'Running'), (OK, 'Finished'), (FAILED, 'Failed')]

    started = models.DateTimeField(default=timezone.now, db_index=True)
    finished = models.DateTimeField(null=True, default=None)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=RUNNING,
                              db_index=True)

    books_added = models.IntegerField(default=0)
    books_deleted = models.IntegerField(default=0)
    books_skipped = models.IntegerField(default=0)
    bad_books = models.IntegerField(default=0)
    books_in_archives = models.IntegerField(default=0)
    arch_scanned = models.IntegerField(default=0)
    arch_skipped = models.IntegerField(default=0)
    bad_archives = models.IntegerField(default=0)

    error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-started']

    @property
    def duration_seconds(self):
        if not self.finished:
            return None
        return (self.finished - self.started).total_seconds()

    def __str__(self):
        return 'scan %s %s' % (self.started.isoformat(), self.status)


class ScanSeen(models.Model):
    """Scratch list of book ids seen during the current library scan.

    Replaces the old full-table `avail=1` flip: the scanner records here each
    book it re-finds or adds, and the post-walk sweep deletes the books NOT
    listed (anti-join). It is a plain id column, not a ForeignKey, so recording
    an id is cheap and never rewrites the (large) Book table; rows are cleared
    at the start of every scan. Access is serialized by the scanner's advisory
    lock, so a single shared table is safe.
    """
    book_id = models.BigIntegerField(primary_key=True)

    class Meta:
        db_table = 'opds_catalog_scan_seen'
