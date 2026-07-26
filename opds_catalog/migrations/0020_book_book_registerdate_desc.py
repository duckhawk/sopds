# Index for the "recently added" feed and page, which page through the whole
# catalogue with
#   SELECT ... FROM opds_catalog_book ORDER BY registerdate DESC, id DESC
#     LIMIT %s OFFSET %s
# Without it that is a full sort of the table for every page. The index is
# declared on the model (unlike the raw-SQL ones in 0011/0012/0016) because it
# is a plain expression index Django emits portably, so the test sqlite DB gets
# it too.
#
# NB on a large existing catalogue this CREATE INDEX takes a write lock on
# opds_catalog_book for the duration; run it during a scan-free window.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('opds_catalog', '0019_book_isbn'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='book',
            index=models.Index(models.OrderBy(models.F('registerdate'), descending=True), models.OrderBy(models.F('id'), descending=True), name='book_registerdate_desc'),
        ),
    ]
