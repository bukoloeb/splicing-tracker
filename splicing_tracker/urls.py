from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    # Include Django's auth URLs for login/logout
    path('accounts/', include('django.contrib.auth.urls')),
    # Include the splicing application's URLs as the root path
    path('', include('splicing.urls')),
]

# Serve Media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# CRITICAL FIX: Serve Static files in production (when DEBUG=False)
# This forces Django to find the CSS files even if the middleware fails.
if not settings.DEBUG:
    urlpatterns += [
        re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),
    ]