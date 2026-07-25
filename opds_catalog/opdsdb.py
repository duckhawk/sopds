# -*- coding: utf-8 -*-

import os
import re

from django.utils.translation import gettext as _ , gettext_noop as _noop
from django.db import transaction, connection

from opds_catalog.models import Book, Catalog, Author, Genre, Series, bseries, bauthor, bgenre, LangCodes, ScanSeen
from opds_catalog.models import SIZE_BOOK_FILENAME, SIZE_BOOK_PATH, SIZE_BOOK_FORMAT, SIZE_BOOK_DOCDATE, SIZE_BOOK_LANG, SIZE_BOOK_TITLE, SIZE_BOOK_ANNOTATION, SIZE_BOOK_ISBN
from opds_catalog.models import SIZE_CAT_CATNAME, SIZE_CAT_PATH, SIZE_AUTHOR_NAME, SIZE_GENRE, SIZE_GENRE_SUBSECTION, SIZE_SERIES


##########################################################################
# типы каталогов (cat_type)
#
CAT_NORMAL=0
CAT_ZIP=1
CAT_INPX=2
CAT_INP=3

##########################################################################
# Как будем искать дубликаты
#
CMP_NONE=0
CMP_NORMAL=1
CMP_STRONG=2
CMP_CLEAR=3
CMP_TITLE_FTYPE_FSIZE=2
CMP_TITLE_AUTHORS=1

##########################################################################
# разные константы
#
unknown_genre_en =_noop('Unknown genre') 
unknown_genre=_(unknown_genre_en)

##########################################################################
# объект который мы будем использовать для перекодироки 4х байтного UTF в 3х байтный
# пока только для аннотации, т.к. там уже "словлена" ошибка при записи в 3х байтный utf8 MYSQL
#
utfhigh = re.compile(u'[\U00010000-\U0010ffff]')

def pg_optimize(verbose=False):
    """ TODO: Table optimizations for Postgre """
    if connection.vendor != 'postgresql':
        if verbose:
            print('No PostgreSql connection backend detected...')
    else:
        print('Start PostgreSql tables optimization...')
        cursor = connection.cursor()
        cursor.execute('alter table opds_catalog_book SET ( fillfactor = 50)')
        cursor.execute('VACUUM FULL opds_catalog_book')
        print('PostgreSql tables internal structure optimized...')

def vacuum_analyze(verbose=False):
    """VACUUM ANALYZE the large catalog tables (PostgreSQL only).

    Run after a scan: the full-table ``avail`` sweep churns every row, so
    without this the tables accumulate dead tuples and a stale visibility map
    until autovacuum catches up, during which the alphabet menu loses its
    Index-Only Scan and slows to tens of seconds. VACUUM (not FULL) is
    non-blocking. Safe to skip on non-PostgreSQL backends.
    """
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        for table in ('opds_catalog_book', 'opds_catalog_author', 'opds_catalog_series'):
            if verbose:
                print('VACUUM (ANALYZE) %s ...' % table)
            cursor.execute('VACUUM (ANALYZE) %s' % table)


def clear_all(verbose=False):
    cursor = connection.cursor()
    cursor.execute('delete from opds_catalog_bseries')
    cursor.execute('delete from opds_catalog_bauthor')
    cursor.execute('delete from opds_catalog_bgenre')
    cursor.execute('delete from opds_catalog_bookshelf')
    cursor.execute('delete from opds_catalog_book')
    cursor.execute('delete from opds_catalog_catalog')
    cursor.execute('delete from opds_catalog_author')
    cursor.execute('delete from opds_catalog_genre')
    cursor.execute('delete from opds_catalog_series')
    cursor.execute('delete from opds_catalog_counter')
    
def clear_genres(verbose=False):
    cursor = connection.cursor()
    cursor.execute('delete from opds_catalog_genre')

# Incremental-scan bookkeeping.
#
# Old scheme: flip every non-deleted book to avail=1 up front, mark the ones
# re-found during the walk back to avail=2, then delete everything left at
# avail<=1. That start-of-scan `UPDATE opds_catalog_book SET avail=1` rewrote
# the whole table on every scan (bloat + slow reads while scanning, and it was
# the query that got orphaned and ran for hours).
#
# New scheme: don't touch the Book table up front at all. Record the id of each
# book re-found or added into the scratch `ScanSeen` table (see mark_seen /
# _mark_seen_qs), then delete the books NOT recorded (scan_finish, an anti-join
# that only touches the actually-vanished rows). `avail` is kept only for the
# logical-delete mode (avail=0) and is not read by the OPDS/web output.

SEEN_BATCH = 5000


def scan_begin():
    """Start a scan pass: clear the scratch table of seen book ids.

    Replaces avail_check_prepare()'s full-table `UPDATE avail=1` — nothing on
    opds_catalog_book is written, so unchanged books are never rewritten.
    """
    ScanSeen.objects.all().delete()


def mark_seen(book_id):
    """Record a single book id as present in this scan pass."""
    ScanSeen.objects.bulk_create([ScanSeen(book_id=book_id)], ignore_conflicts=True)


def _mark_seen_qs(book_qs):
    """Record every book matched by `book_qs` as seen; return how many.

    Used by the archive skip fast-paths: an unchanged archive's books are all
    still present, so mark them without re-reading the archive.
    """
    ids = list(book_qs.values_list('id', flat=True))
    if ids:
        ScanSeen.objects.bulk_create(
            (ScanSeen(book_id=i) for i in ids), ignore_conflicts=True, batch_size=SEEN_BATCH)
    return len(ids)


def scan_finish(logical=False):
    """Remove books not seen during this scan (anti-join on ScanSeen).

    Physical delete relies on the ORM to cascade the m2m/bookshelf rows; the
    logical mode just marks them avail=0. Deletes in id chunks so a large
    delete set can't blow the SQL parameter limit.

    Safety: if nothing was recorded as seen while the catalogue is non-empty,
    refuse to delete — a real scan of a non-empty library always sees at least
    one book, so an empty seen set means the scan didn't run (never wipe the
    whole catalogue).
    """
    if ScanSeen.objects.count() == 0 and Book.objects.exists():
        return 0

    gone_ids = list(Book.objects.exclude(id__in=ScanSeen.objects.values('book_id'))
                    .values_list('id', flat=True))
    if not gone_ids:
        return 0

    for start in range(0, len(gone_ids), SEEN_BATCH):
        chunk = gone_ids[start:start + SEEN_BATCH]
        if logical:
            Book.objects.filter(id__in=chunk).update(avail=0)
        else:
            Book.objects.filter(id__in=chunk).delete()
    return len(gone_ids)


def p(s,size):
    new = utfhigh.sub(u'',s[:size])
    return new
    
def getlangcode(s):
    langcode = 9
    if len(s)==0:
        return langcode 
    for k in LangCodes.keys():
        if s[0] in LangCodes[k]:
            langcode = k
    
    return langcode
    
def arc_skip(arcpath,arcsize):
    """
       Выясняем изменялся ли архив (ZIP или INP-файл)
       если нет, то пытаемся пропустить сканирование, помечая все книги из
       архива как увиденные (seen)
       Если не одной такой книги не нашлось, то считаем что пропуск сканирования не удался
       и возвращаем 0
       Если книги из искомого каталога имелись и помечены seen, то пропуск возможен
       и возвращаем 1 (или row_count)
    """
    catalog = findcat(arcpath)

    # Если такого каталога еще нет в БД, то значит считаем что ZIP изменен и пропуск невозможен
    if catalog == None:
        return 0

    # Если каталог в БД найден и его размер совпадает с текущим, то считаем что файл архива не менялся
    # Поэтому помечаем все книги из этого архива как увиденные; если таких книг нет,
    # то видимо нужно пересканировать архив
    if arcsize == catalog.cat_size:
        return _mark_seen_qs(Book.objects.filter(path=arcpath))

    # Здесь мы оказываемся если размеры архива в БД и в наличии разные, поэтому считаем что изменения в архиве есть
    # и пропуск сканирования невозможен
    return 0


def inp_skip(arcpath,arcsize):
    """
       Выясняем изменялся ли INPX-файл)
       если нет, то пытаемся пропустить сканирование, устанавливая для всех книг из
       INPX avail=2
       Если не одной такой книги не нашлось, то считаем что пропуск сканирования не удался
       и возвращаем 0
       Если книги из искомого INPX имелись и для них установлен avail=2, то пропуск возможен 
       и возвращаем 1 (или row_count)      
    """
    catalog = findcat(arcpath)

    # Если такого INPX еще нет в БД, то значит считаем что INPX изменен и пропуск невозможен
    if catalog == None:
        return 0

    # Если INPX в БД найден и его размер совпадает с текущим, то считаем что файл INPX не менялся
    # Поэтому делаем update всех книг из этого INPX, однако если ни одного изменения не произошло, то
    # таких книг нет, поэтому видимо нужно пересканировать архив
    if arcsize == catalog.cat_size:
        return _mark_seen_qs(Book.objects.filter(catalog__parent=catalog))

    # Здесь мы оказываемся если размеры INPX в БД и в наличии разные, поэтому считаем что изменения в архиве есть
    # и пропуск сканирования невозможен
    return 0


def inpx_skip(arcpath, arcsize):
    """
       Выясняем изменялся ли INPX-файл)
       если нет, то пытаемся пропустить сканирование, устанавливая для всех книг из
       INPX avail=2
       Если не одной такой книги не нашлось, то считаем что пропуск сканирования не удался
       и возвращаем 0
       Если книги из искомого INPX имелись и для них установлен avail=2, то пропуск возможен
       и возвращаем 1 (или row_count)
    """
    catalog = findcat(arcpath)

    # Если такого INPX еще нет в БД, то значит считаем что INPX изменен и пропуск невозможен
    if catalog == None:
        return 0

    # Если INPX в БД найден и его размер совпадает с текущим, то считаем что файл INPX не менялся
    # Поэтому делаем update всех книг из этого INPX, однако если ни одного изменения не произошло, то
    # таких книг нет, поэтому видимо нужно пересканировать архив
    if arcsize == catalog.cat_size:
        return _mark_seen_qs(Book.objects.filter(catalog__parent__parent=catalog))

    # Здесь мы оказываемся если размеры INPX в БД и в наличии разные, поэтому считаем что изменения в архиве есть
    # и пропуск сканирования невозможен
    return 0

def findcat(cat_name):
    (head,tail)=os.path.split(cat_name)
    # .first() (not .get()): there is no DB unique constraint on (cat_name,
    # path), and a duplicate row (e.g. from an interrupted/overlapping scan or
    # a truncation collision) made .get() raise MultipleObjectsReturned and
    # abort the whole scan.
    return Catalog.objects.filter(cat_name=tail[:SIZE_CAT_CATNAME], path=cat_name[:SIZE_CAT_PATH]).first()

def addcattree(cat_name, archive=0, size = 0):
    catalog = findcat(cat_name)
    if catalog:
        return catalog
    if cat_name in ("","."):
        return Catalog.objects.get_or_create(parent=None, cat_name=".", path=".", cat_type=0)[0]
    (head,tail)=os.path.split(cat_name)
    parent=addcattree(head)
    new_cat = Catalog.objects.create(parent=parent, cat_name=tail[:SIZE_CAT_CATNAME], path=cat_name[:SIZE_CAT_PATH], cat_type=archive, cat_size=size)

    return new_cat

def findbook(name, path, setavail=0):
    # Здесь специально не делается проверка avail, т.к. если удаление было логическим,
    # а книга была восстановлена в своем старом месте
    # то произойдет восстановление записи об этой книги а не добавится новая
    # .first() (not .get()): no DB unique constraint exists on (filename, path),
    # so a duplicate row would otherwise raise MultipleObjectsReturned and abort
    # the scan.
    book = Book.objects.filter(filename=name[:SIZE_BOOK_FILENAME], path=path[:SIZE_BOOK_PATH]).first()

    # `setavail` keeps its old meaning "this book is present in the current
    # scan": record it as seen (no write to the Book row) instead of the old
    # book.avail=2; book.save().
    if book and setavail:
        mark_seen(book.id)

    return book

def addbook(name, path, cat, exten, title, annotation, docdate, lang, size=0, archive=0, isbn=''):
    book = Book.objects.create(filename=name[:SIZE_BOOK_FILENAME],path=path[:SIZE_BOOK_PATH],catalog=cat,filesize=size,format=exten.lower()[:SIZE_BOOK_FORMAT],
                title=title[:SIZE_BOOK_TITLE],search_title=title.upper()[:SIZE_BOOK_TITLE],annotation=p(annotation,SIZE_BOOK_ANNOTATION),
                docdate=docdate[:SIZE_BOOK_DOCDATE],lang=lang[:SIZE_BOOK_LANG],cat_type=archive,avail=2, lang_code=getlangcode(title), isbn=(isbn or '')[:SIZE_BOOK_ISBN])
    # A freshly added book is present in this scan.
    mark_seen(book.id)
    return book

def addauthor(full_name):
    author, created = Author.objects.get_or_create(full_name=full_name[:SIZE_AUTHOR_NAME], defaults={'search_full_name':full_name.upper()[:SIZE_AUTHOR_NAME], 
                                                                                                     'lang_code':getlangcode(full_name)})
    return author

def addbauthor(book, author):
    ba = bauthor(book=book, author=author)
    ba.save()

def addgenre(genre):
    genre, created = Genre.objects.get_or_create(genre=genre[:SIZE_GENRE], defaults={'section':unknown_genre, 'subsection':genre[:SIZE_GENRE_SUBSECTION]})
    return genre

def addbgenre(book, genre):
    bg = bgenre(book=book, genre=genre)
    bg.save()

def addseries(ser):
    series, created = Series.objects.get_or_create(ser=ser[:SIZE_SERIES], defaults={'search_ser':ser.upper()[:SIZE_SERIES], 'lang_code':getlangcode(ser)})
    return series

def addbseries(book, ser, ser_no):
    bs = bseries(book=book, ser=ser, ser_no=ser_no)
    bs.save()
    
def set_autocommit(autocommit):
    transaction.set_autocommit(autocommit)
    
def commit():
    transaction.commit()
