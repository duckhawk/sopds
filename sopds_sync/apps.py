from django.apps import AppConfig


class SopdsSyncConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sopds_sync'
    verbose_name = 'Reading progress sync (KOReader / Moon+ Reader)'
