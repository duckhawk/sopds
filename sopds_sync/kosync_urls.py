from django.urls import re_path

from sopds_sync import kosync

app_name = 'kosync'

urlpatterns = [
    re_path(r'^users/create/?$', kosync.users_create, name='users_create'),
    re_path(r'^users/auth/?$', kosync.users_auth, name='users_auth'),
    re_path(r'^syncs/progress/?$', kosync.progress_update, name='progress_update'),
    re_path(r'^syncs/progress/(?P<document>[0-9a-fA-F]{1,32})/?$', kosync.progress_get, name='progress_get'),
    re_path(r'^healthcheck/?$', kosync.healthcheck, name='healthcheck'),
]
