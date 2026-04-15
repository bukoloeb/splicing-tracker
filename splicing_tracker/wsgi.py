import os
from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'splicing_tracker.settings')

application = get_wsgi_application()

# FORCE WRAP: This bypasses Django's URL routing and handles the files at the server level
application = WhiteNoise(application, root='/opt/render/project/src/staticfiles')
application.add_files('/opt/render/project/src/staticfiles/admin', prefix='admin/')