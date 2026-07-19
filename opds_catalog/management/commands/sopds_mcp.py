# -*- coding: utf-8 -*-
#
# MCP (Model Context Protocol) сервер для каталога SOPDS.
#
# Предоставляет LLM-клиентам инструменты для поиска книг и авторов,
# получения метаданных и скачивания файлов. Переиспользует ORM-модели
# и логику выгрузки (opds_catalog.dl), чтобы поведение совпадало с
# веб-бэкендом и telegram-ботом.
#
# Транспорт по умолчанию - stdio (для локальных MCP-клиентов).
# Запуск: python manage.py sopds_mcp
#
import os

from django.core.management.base import BaseCommand
from django.db import connection, connections, close_old_connections
from django.db.models import Q, Count

from opds_catalog.models import Book, Author, bseries, Counter
from opds_catalog.models import (
    counter_allbooks, counter_allcatalogs, counter_allauthors,
    counter_allgenres, counter_allseries,
)
from opds_catalog import dl
from constance import config

ANNOTATION_PREVIEW = 300


def _reset_stale_connection():
    """Сбрасывает "протухшее" соединение с БД.

    MCP-инструменты выполняются в рабочих потоках anyio, а сервер живёт
    долго - то же самое делает telebot в BookFilter().
    """
    close_old_connections()
    if connection.connection and not connection.is_usable():
        del connections._connections.default


def _search_books_qs(query):
    q_objects = Q()
    q_objects.add(Q(search_title__contains=query.upper()), Q.OR)
    q_objects.add(Q(authors__search_full_name__contains=query.upper()), Q.OR)
    return Book.objects.filter(q_objects).order_by('search_title', '-docdate').distinct()


def _book_brief(book):
    return {
        "id": book.id,
        "title": book.title,
        "authors": [a.full_name for a in book.authors.all()],
        "format": book.format,
        "filesize": book.filesize,
        "docdate": book.docdate,
        "lang": book.lang,
        "avail": book.avail,
    }


def _book_full(book):
    data = _book_brief(book)
    series = [
        {"title": bs.ser.ser, "number": bs.ser_no}
        for bs in bseries.objects.select_related('ser').filter(book=book)
    ]
    data.update({
        "genres": [g.genre for g in book.genres.all()],
        "series": series,
        "annotation": book.annotation,
        "path": book.path,
        "filename": book.filename,
    })
    return data


class Command(BaseCommand):
    help = 'Run MCP (Model Context Protocol) server exposing the books catalog.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--transport', dest='transport', default='stdio',
            choices=['stdio', 'sse', 'streamable-http'],
            help='MCP transport (default: stdio).',
        )

    def handle(self, *args, **options):
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError:
            self.stderr.write(
                "The 'mcp' package is required. Install it with: pip install 'mcp>=1.2.0'"
            )
            return

        mcp = FastMCP("sopds")

        @mcp.tool()
        def search_books(query: str, limit: int = 20, offset: int = 0) -> dict:
            """Search books by title or author name (case-insensitive substring).

            Returns brief metadata for each match, ordered by title.
            Use get_book for full details and download_book to fetch the file.
            """
            _reset_stale_connection()
            query = (query or "").strip()
            if len(query) < 2:
                return {"error": "Query too short (min 2 characters).", "count": 0, "books": []}
            limit = max(1, min(limit, 100))
            offset = max(0, offset)
            qs = _search_books_qs(query)
            total = qs.count()
            books = [_book_brief(b) for b in qs[offset:offset + limit]]
            return {"count": total, "offset": offset, "limit": limit, "books": books}

        @mcp.tool()
        def get_book(book_id: int) -> dict:
            """Return full metadata for a single book by its id.

            Includes authors, genres, series (with number), annotation and format.
            """
            _reset_stale_connection()
            try:
                book = Book.objects.get(id=book_id)
            except Book.DoesNotExist:
                return {"error": "Book not found.", "book_id": book_id}
            return _book_full(book)

        @mcp.tool()
        def search_authors(query: str, limit: int = 20) -> dict:
            """Search authors by name (case-insensitive substring).

            Returns each author's id, full name and number of available books.
            """
            _reset_stale_connection()
            query = (query or "").strip()
            if len(query) < 2:
                return {"error": "Query too short (min 2 characters).", "count": 0, "authors": []}
            limit = max(1, min(limit, 100))
            qs = (Author.objects
                  .filter(search_full_name__contains=query.upper())
                  .annotate(book_count=Count('book'))
                  .order_by('search_full_name'))
            total = qs.count()
            authors = [
                {"id": a.id, "full_name": a.full_name, "book_count": a.book_count}
                for a in qs[:limit]
            ]
            return {"count": total, "limit": limit, "authors": authors}

        @mcp.tool()
        def get_stats() -> dict:
            """Return catalog statistics: total books, catalogs, authors, genres,
            series and the timestamp of the last library scan."""
            _reset_stale_connection()
            lastscan = Counter.objects.get_lastscan()
            return {
                "books": Counter.objects.get_counter(counter_allbooks),
                "catalogs": Counter.objects.get_counter(counter_allcatalogs),
                "authors": Counter.objects.get_counter(counter_allauthors),
                "genres": Counter.objects.get_counter(counter_allgenres),
                "series": Counter.objects.get_counter(counter_allseries),
                "last_scan": lastscan.isoformat() if lastscan else None,
            }

        @mcp.tool()
        def download_book(book_id: int, format: str = "orig", output_dir: str = "") -> dict:
            """Extract a book file and save it to disk, returning the saved path.

            format: 'orig' (original file), 'zip' (original zipped),
            'epub' or 'mobi' (converted from fb2, converters must be configured).
            output_dir: target directory; defaults to SOPDS_TEMP_DIR.
            """
            _reset_stale_connection()
            try:
                book = Book.objects.get(id=book_id)
            except Book.DoesNotExist:
                return {"error": "Book not found.", "book_id": book_id}

            fmt = (format or "orig").lower()
            # getFileName derives from book metadata (title/filename), which is
            # attacker-influenceable; strip any path components so the output
            # cannot escape target_dir via '..' or an absolute path.
            filename = os.path.basename(dl.getFileName(book)) or str(book.id)
            if fmt == "orig":
                document = dl.getFileData(book)
            elif fmt == "zip":
                document = dl.getFileDataZip(book)
                filename = filename + ".zip"
            elif fmt == "epub":
                document = dl.getFileDataEpub(book)
                filename = filename.replace(".fb2", ".epub")
            elif fmt == "mobi":
                document = dl.getFileDataMobi(book)
                filename = filename.replace(".fb2", ".mobi")
            else:
                return {"error": "Unknown format. Use one of: orig, zip, epub, mobi."}

            if not document:
                return {"error": "Failed to extract file (missing source or converter)."}

            target_dir = output_dir.strip() or config.SOPDS_TEMP_DIR
            os.makedirs(target_dir, exist_ok=True)
            out_path = os.path.join(target_dir, os.path.basename(filename))
            real_dir = os.path.realpath(target_dir)
            if os.path.commonpath([real_dir, os.path.realpath(out_path)]) != real_dir:
                return {"error": "Refusing to write outside the target directory."}
            data = document.read()
            document.close()
            with open(out_path, "wb") as fw:
                fw.write(data)

            return {
                "book_id": book.id,
                "title": book.title,
                "format": fmt,
                "filename": filename,
                "path": out_path,
                "size": len(data),
            }

        if options['transport'] != 'stdio':
            self.stderr.write(
                "WARNING: the '%s' transport exposes the catalog (including file "
                "download/write tools) over HTTP with no authentication. Only run "
                "it behind an authenticating reverse proxy; never bind it to a "
                "public interface." % options['transport']
            )
        self.stdout.write("Starting SOPDS MCP server (transport=%s)..." % options['transport'])
        mcp.run(transport=options['transport'])
