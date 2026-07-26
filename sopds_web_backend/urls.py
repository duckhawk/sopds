
from django.urls import re_path, reverse_lazy
from django.contrib.auth import views as auth_views
from sopds_web_backend import views

app_name = 'opds_web_backend'

urlpatterns = [
    re_path(r'^search/books/$',views.SearchBooksView, name='searchbooks'),
    re_path(r'^search/authors/$',views.SearchAuthorsView, name='searchauthors'),
    re_path(r'^search/series/$',views.SearchSeriesView, name='searchseries'),
    re_path(r'^search/genres/$',views.SearchGenresView, name='searchgenres'),
    re_path(r'^search/suggest/$',views.SearchSuggestView, name='suggest'),
    re_path(r'^catalog/$',views.CatalogsView, name='catalog'),
    re_path(r'^book/$',views.BooksView, name='book'),
    re_path(r'^book/read/(?P<book_id>[0-9]+)/$',views.BookReaderView, name='read'),
    re_path(r'^author/$',views.AuthorsView, name='author'),
    re_path(r'^genre/$',views.GenresView, name='genre'),
    re_path(r'^series/$',views.SeriesView, name='series'),
    re_path(r'^theme/$',views.ThemeView, name='theme'),
    re_path(r'^settings/$',views.SettingsView, name='settings'),
    re_path(r'^sync/$',views.DeviceSyncView, name='devicesync'),
    re_path(r'^login/$',views.LoginView, name='login'),

    # Self-service accounts. The reset flow is Django's own — the security is in
    # the token generation and expiry, which is not worth reimplementing.
    re_path(r'^register/$', views.RegisterView, name='register'),
    re_path(r'^password/change/$', views.SopdsPasswordChangeView.as_view(), name='password_change'),
    re_path(r'^password/change/done/$', auth_views.PasswordChangeDoneView.as_view(
        template_name='sopds_password_change_done.html'), name='password_change_done'),
    re_path(r'^password/reset/$', views.SopdsPasswordResetView.as_view(), name='password_reset'),
    re_path(r'^password/reset/sent/$', auth_views.PasswordResetDoneView.as_view(
        template_name='sopds_password_reset_done.html'), name='password_reset_done'),
    re_path(r'^password/reset/(?P<uidb64>[0-9A-Za-z_\-]+)/(?P<token>[^/]+)/$',
            auth_views.PasswordResetConfirmView.as_view(
                template_name='sopds_password_reset_confirm.html',
                success_url=reverse_lazy('web:password_reset_complete')),
            name='password_reset_confirm'),
    re_path(r'^password/reset/done/$', auth_views.PasswordResetCompleteView.as_view(
        template_name='sopds_password_reset_complete.html'), name='password_reset_complete'),
    re_path(r'^oidc/login/$',views.OIDCLoginView, name='oidc_login'),
    re_path(r'^oidc/callback/$',views.OIDCCallbackView, name='oidc_callback'),
    re_path(r'^logout/$',views.LogoutView, name='logout'),
    re_path(r'^bs/add/$',views.BSAddView, name='bsadd'),
    re_path(r'^bs/delete/$',views.BSDelView, name='bsdel'),
    re_path(r'^bs/clear/$', views.BSClearView, name='bsclear'),
    re_path(r'^bs/status/(?P<book_id>[0-9]+)/$', views.BSSetStatus, name='bsstatus'),
    re_path(r'^bs/rating/(?P<book_id>[0-9]+)/$', views.BSSetRating, name='bsrating'),
    re_path(r'^bs/setpos/(?P<book_id>[0-9]+)/$', views.BSSetPos, name='setpos'),
    re_path(r'^bs/getpos/(?P<book_id>[0-9]+)/$', views.BSGetPos, name='getpos'),
    re_path(r'^$',views.hello, name='main'),
]

#handler403 = 'views.handler403'
