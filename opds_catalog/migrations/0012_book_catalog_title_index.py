# Composite index for the catalog browse page (/web/catalog and the OPDS
# CatalogsFeed): Book.objects.filter(catalog=cat).order_by('search_title').
# Without (catalog_id, search_title) Postgres must filter by catalog and then
# sort the whole matching set by search_title, which is slow for catalogs that
# contain many books. With the composite index it does an ordered index range
# scan and can satisfy LIMIT/OFFSET directly.
#
# PostgreSQL only, guarded by connection.vendor (mirrors migration 0011).
from django.db import migrations


INDEX_NAME = "opds_catalog_book_catalog_title"
TABLE = "opds_catalog_book"


def create_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS "%s" ON "%s" ("catalog_id", "search_title")'
        % (INDEX_NAME, TABLE)
    )


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute('DROP INDEX IF EXISTS "%s"' % INDEX_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ("opds_catalog", "0011_search_trigram_indexes"),
    ]

    operations = [
        migrations.RunPython(create_index, drop_index),
    ]
