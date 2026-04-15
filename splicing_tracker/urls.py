from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from django.contrib.auth import views as auth_views
from splicing.views import MyPasswordChangeView
urlpatterns = [
    path('admin/', admin.site.urls),

    path('accounts/password_change/', MyPasswordChangeView.as_view(), name='password_change'),

    path('accounts/password_change/done/', auth_views.PasswordChangeDoneView.as_view(), name='password_change_done'),    path('accounts/password_reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(),
         name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),

    # Include remaining Django auth URLs (login, logout, etc.)
    path('accounts/', include('django.contrib.auth.urls')),

    # Splicing application URLs
    path('', include('splicing.urls')),
]

# Serve Media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# CRITICAL FIX: Serve Static files in production (when DEBUG=False)
if not settings.DEBUG:
    urlpatterns += [
        re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),
    ]