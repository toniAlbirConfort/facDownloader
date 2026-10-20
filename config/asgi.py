Attempting to perform the InitializeDefaultDrives operation on the 'FileSystem' provider failed.
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_asgi_application()

