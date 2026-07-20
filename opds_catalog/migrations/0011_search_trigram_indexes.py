# Adds pg_trgm GIN indexes to speed up substring/prefix search on the columns
# used by the search views (search_title__contains / __startswith, etc.).
# A single GIN trigram index accelerates both LIKE '%term%' and LIKE 'term%',
# which a plain btree index cannot do for the leading-wildcard case.
#
# PostgreSQL only — guarded by connection.vendor so the local sqlite/mysql
# setups keep working (they just skip these indexes).
from django.db import migrations


# (index_name, table, column)
PG_TRGM_INDEXES = [
    ("opds_catalog_book_search_title_trgm", "opds_catalog_book", "search_title"),
    ("opds_catalog_author_search_name_trgm", "opds_catalog_author", "search_full_name"),
    ("opds_catalog_series_search_ser_trgm", "opds_catalog_series", "search_ser"),
]


def create_trgm_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, table, column in PG_TRGM_INDEXES:
        schema_editor.execute(
            'CREATE INDEX IF NOT EXISTS "%s" ON "%s" USING gin ("%s" gin_trgm_ops)'
            % (name, table, column)
        )


def drop_trgm_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for name, _table, _column in PG_TRGM_INDEXES:
        schema_editor.execute('DROP INDEX IF EXISTS "%s"' % name)


class Migration(migrations.Migration):

    dependencies = [
        ("opds_catalog", "0010_alter_bookshelf_unique_together"),
    ]

    operations = [
        migrations.RunPython(create_trgm_indexes, drop_trgm_indexes),
    ]
