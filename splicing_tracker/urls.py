from django.contrib import admin
from django.urls import path, include
from django.conf import settings # Import settings
from django.conf.urls.static import static # Import static
urlpatterns = [
    path('admin/', admin.site.urls),
    # Include Django's auth URLs for login/logout
    path('accounts/', include('django.contrib.auth.urls')),
    # Include the splicing application's URLs as the root path
    path('', include('splicing.urls')), 
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)