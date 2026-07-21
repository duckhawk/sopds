from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("opds_catalog", "0014_theme_reader_prefs"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookshelf",
            name="status",
            field=models.CharField(
                blank=True, default="",
                choices=[("", "—"), ("to_read", "To read"), ("reading", "Reading"), ("read", "Read")],
                max_length=16),
        ),
        migrations.AddField(
            model_name="bookshelf",
            name="rating",
            field=models.PositiveSmallIntegerField(default=None, null=True),
        ),
    ]
