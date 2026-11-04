from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Local development: password-reset emails are printed to the Django console.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Cache: use in-memory cache for local development and tests without external Redis daemon
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tci-dev-cache",
    }
}

