from django.contrib import admin
from opds_catalog.models import Genre, Book, Author, Series

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

admin.site.register(Genre, Genre_admin)
admin.site.register(Book, Book_admin)
admin.site.register(Author, Author_admin)
admin.site.register(Series, Series_admin)
