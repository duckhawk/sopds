from django.contrib import admin
from opds_catalog.models import Genre, Book, Author, Series, ScanRun

# Register your models here.
class Genre_admin(admin.ModelAdmin):
    list_display = ('genre', 'section', 'subsection')

class Book_admin(admin.ModelAdmin):
    list_display = ('title', 'format', 'lang', 'avail', 'registerdate')
    list_filter = ('format', 'lang', 'avail', 'cat_type')
    search_fields = ('title', 'filename')
    raw_id_fields = ('catalog',)
    date_hierarchy = 'registerdate'

class Author_admin(admin.ModelAdmin):
    list_display = ('full_name', 'lang_code')
    search_fields = ('full_name',)

class Series_admin(admin.ModelAdmin):
    list_display = ('ser', 'lang_code')
    search_fields = ('ser',)

class ScanRun_admin(admin.ModelAdmin):
    """Read-only history of library scans.

    Rows are written by the scanner, never by hand — editing one would only
    misreport what happened — so the admin offers no add, change or delete.
    """
    list_display = ('started', 'status', 'took', 'books_added', 'books_deleted',
                    'bad_books', 'bad_archives')
    list_filter = ('status',)
    date_hierarchy = 'started'

    @admin.display(description='duration')
    def took(self, obj):
        seconds = obj.duration_seconds
        if seconds is None:
            return '—'
        return '%d:%02d:%02d' % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(ScanRun, ScanRun_admin)
admin.site.register(Genre, Genre_admin)
admin.site.register(Book, Book_admin)
admin.site.register(Author, Author_admin)
admin.site.register(Series, Series_admin)
