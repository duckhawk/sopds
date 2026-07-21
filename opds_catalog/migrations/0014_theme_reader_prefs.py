from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("opds_catalog", "0013_bookshelf_position_char"),
    ]

    operations = [
        migrations.AddField(
            model_name="theme",
            name="reader_mode",
            field=models.CharField(
                choices=[("whole", "Whole text"), ("chapters", "By chapters")],
                default="whole", max_length=16),
        ),
        migrations.AddField(
            model_name="theme",
            name="font_size",
            field=models.PositiveSmallIntegerField(default=100),
        ),
    ]
