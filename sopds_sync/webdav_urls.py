from django.urls import re_path

from sopds_sync import webdav

app_name = 'webdav'

urlpatterns = [
    re_path(r'^(?P<path>.*)$', webdav.dav, name='dav'),
]
