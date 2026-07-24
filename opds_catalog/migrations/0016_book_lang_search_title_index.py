# Composite index for the alphabet "select by substring" menu (BooksView /
# AuthorsView / SeriesView and the OPDS *Feed equivalents), which run
#   SELECT substr(search_title,1,1), count(*) FROM opds_catalog_book
#   WHERE lang_code = %s [AND search_title LIKE %s] GROUP BY substr(...)
# via opds_catalog.utils.alphabet_menu.
#
# Without (lang_code, search_title) Postgres scanned the lang_code btree and
# then did ~33k random heap fetches to read search_title for the substr()
# grouping (or seq-scanned the whole 3 GB table), ~17-21 s cold on a large
# catalog. With this composite index the query is an Index Only Scan
# (Heap Fetches: 0) grouped in index order -> sub-second.
#
# PostgreSQL only, guarded by connection.vendor (mirrors migrations 0011/0012).
from django.db import migrations


INDEX_NAME = "opds_catalog_book_lang_stitle"
TABLE = "opds_catalog_book"


def create_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS "%s" ON "%s" ("lang_code", "search_title")'
        % (INDEX_NAME, TABLE)
    )


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute('DROP INDEX IF EXISTS "%s"' % INDEX_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ("opds_catalog", "0015_bookshelf_status_rating"),
    ]

    operations = [
        migrations.RunPython(create_index, drop_index),
    ]
