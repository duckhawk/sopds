import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("opds_catalog", "0013_bookshelf_position_char"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookshelf",
            name="position_percent",
            field=models.FloatField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="bookshelf",
            name="position_time",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.CreateModel(
            name="GDriveAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("refresh_token", models.CharField(max_length=512)),
                ("email", models.CharField(default=None, max_length=254, null=True)),
                ("cache_folder_id", models.CharField(default=None, max_length=128, null=True)),
                ("created", models.DateTimeField(default=django.utils.timezone.now)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
