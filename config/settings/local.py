"""
Local development settings for loanguard_ai project.
"""

from .base import *

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

# Development specific apps
try:
    import debug_toolbar  # noqa: F401
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = ["127.0.0.1", "localhost"]
except ImportError:
    pass

try:
    import django_browser_reload  # noqa: F401
    INSTALLED_APPS += ["django_browser_reload"]
    MIDDLEWARE.append("django_browser_reload.middleware.BrowserReloadMiddleware")
except ImportError:
    pass
