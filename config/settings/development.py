from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Local development: password-reset emails are printed to the Django console.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
