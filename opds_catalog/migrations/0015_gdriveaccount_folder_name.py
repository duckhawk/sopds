from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("opds_catalog", "0014_gdrive_sync"),
    ]

    operations = [
        migrations.AddField(
            model_name="gdriveaccount",
            name="folder_name",
            field=models.CharField(default=None, max_length=256, null=True),
        ),
    ]
