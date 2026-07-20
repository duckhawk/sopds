from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("opds_catalog", "0012_book_catalog_title_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bookshelf",
            name="position",
            field=models.CharField(default=None, max_length=32, null=True),
        ),
    ]
