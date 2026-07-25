# Scratch table for the incremental-scan "seen book ids" set. Replaces the old
# full-table `UPDATE opds_catalog_book SET avail=1` sweep at the start of every
# scan (which rewrote every row -> bloat + slow reads during a scan).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("opds_catalog", "0016_book_lang_search_title_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScanSeen",
            fields=[
                ("book_id", models.BigIntegerField(primary_key=True, serialize=False)),
            ],
            options={
                "db_table": "opds_catalog_scan_seen",
            },
        ),
    ]
