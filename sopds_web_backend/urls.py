
from django.urls import re_path
from sopds_web_backend import views

app_name = 'opds_web_backend'

urlpatterns = [
    re_path(r'^search/books/$',views.SearchBooksView, name='searchbooks'),
    re_path(r'^search/authors/$',views.SearchAuthorsView, name='searchauthors'),
    re_path(r'^search/series/$',views.SearchSeriesView, name='searchseries'),
    re_path(r'^catalog/$',views.CatalogsView, name='catalog'),
    re_path(r'^book/$',views.BooksView, name='book'),
    re_path(r'^book/read/(?P<book_id>[0-9]+)/$',views.BookReaderView, name='read'),
    re_path(r'^author/$',views.AuthorsView, name='author'),
    re_path(r'^genre/$',views.GenresView, name='genre'),
    re_path(r'^series/$',views.SeriesView, name='series'),
    re_path(r'^theme/$',views.ThemeView, name='theme'),
    re_path(r'^login/$',views.LoginView, name='login'),
    re_path(r'^logout/$',views.LogoutView, name='logout'),
    re_path(r'^bs/add/$',views.BSAddView, name='bsadd'),
    re_path(r'^bs/delete/$',views.BSDelView, name='bsdel'),
    re_path(r'^bs/clear/$', views.BSClearView, name='bsclear'),
    re_path(r'^bs/setpos/(?P<book_id>[0-9]+)/$', views.BSSetPos, name='setpos'),
    re_path(r'^bs/getpos/(?P<book_id>[0-9]+)/$', views.BSGetPos, name='getpos'),
    re_path(r'^gdrive/$', views.GDriveView, name='gdrive'),
    re_path(r'^gdrive/connect/$', views.GDriveConnectView, name='gdrive_connect'),
    re_path(r'^gdrive/callback/$', views.GDriveCallbackView, name='gdrive_callback'),
    re_path(r'^gdrive/disconnect/$', views.GDriveDisconnectView, name='gdrive_disconnect'),
    re_path(r'^gdrive/pickertoken/$', views.GDrivePickerToken, name='gdrive_pickertoken'),
    re_path(r'^gdrive/setfolder/$', views.GDriveSetFolder, name='gdrive_setfolder'),
    re_path(r'^gdrive/pull/(?P<book_id>[0-9]+)/$', views.GDrivePull, name='gdrive_pull'),
    re_path(r'^gdrive/push/(?P<book_id>[0-9]+)/$', views.GDrivePush, name='gdrive_push'),
    re_path(r'^$',views.hello, name='main'),
]

#handler403 = 'views.handler403'
