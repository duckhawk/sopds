from django.contrib import admin

from .models import KosyncCredential, KosyncProgress


@admin.register(KosyncCredential)
class KosyncCredentialAdmin(admin.ModelAdmin):
    list_display = ('user', 'created')
    search_fields = ('user__username',)
    # auth_key is a password-equivalent secret; never surface it in the admin.
    exclude = ('auth_key',)
    readonly_fields = ('created',)


@admin.register(KosyncProgress)
class KosyncProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'document', 'percentage', 'device', 'timestamp')
    search_fields = ('user__username', 'document')
    list_filter = ('device',)
    readonly_fields = ('timestamp',)
