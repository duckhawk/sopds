from django.urls import re_path

from sopds_docs import views

app_name = 'sopds_docs'

urlpatterns = [
    re_path(r'^$', views.index, name='index'),
    # The slug shape is the same one the filenames are validated against, so a
    # path that cannot name a page never reaches the view.
    re_path(r'^(?P<slug>[a-z0-9-]+)/$', views.page, name='page'),
]
