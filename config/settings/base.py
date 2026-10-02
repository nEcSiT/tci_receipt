import os
import sys
from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "apps"))

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, ""),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DATABASE_URL=(str, f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
    REDIS_URL=(str, "redis://localhost:6379/0"),
    CELERY_BROKER_URL=(str, "redis://localhost:6379/0"),
    CELERY_RESULT_BACKEND=(str, "redis://localhost:6379/0"),
    SYSTEM_ADMIN_EMAIL=(str, ""),
    SYSTEM_ADMIN_INITIAL_PASSWORD=(str, ""),
    SYSTEM_ADMIN_RECOVERY_EMAIL=(str, ""),
    SYSTEM_ADMIN_RECOVERY_PHONE=(str, ""),
    STORAGE_BACKEND=(str, "local"),
    STORAGE_DIR=(str, str(BASE_DIR / "media")),
    PAYMENT_PROVIDER_BACKEND=(str, "apps.contributions.providers.mock.MockPaymentProvider"),
    SMS_PROVIDER_BACKEND=(str, "apps.notifications.providers.mock.MockSmsProvider"),
    CHURCH_MERCHANT_NUMBERS=(list, ["HLC_MERCHANT", "0240000000", "0550000000"]),
    PAYMENT_WEBHOOK_SECRET=(str, ""),
    RECEIPT_ACCESS_TOKEN_MAX_AGE=(int, 86400),
)

env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be configured through deployment environment.")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Domain apps (modular monolith)
    "apps.core.apps.CoreConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.members.apps.MembersConfig",
    "apps.contributions.apps.ContributionsConfig",
    "apps.receipts.apps.ReceiptsConfig",
    "apps.requisitions.apps.RequisitionsConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.audit.apps.AuditConfig",
    "apps.reports.apps.ReportsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.audit.middleware.AuditMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.permissions_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

AUTH_USER_MODEL = "accounts.User"

# Authentication URLs
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("STORAGE_DIR"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Shared cache for login throttling and other short-lived application state.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
    }
}

# Session Security
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=1800)  # 30 minutes
SESSION_EXPIRE_AT_BROWSER_CLOSE = env.bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", default=True)
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# Celery Configuration
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Protected System Secrets
SYSTEM_ADMIN_EMAIL = env("SYSTEM_ADMIN_EMAIL")
SYSTEM_ADMIN_INITIAL_PASSWORD = env("SYSTEM_ADMIN_INITIAL_PASSWORD")
SYSTEM_ADMIN_RECOVERY_EMAIL = env("SYSTEM_ADMIN_RECOVERY_EMAIL")
SYSTEM_ADMIN_RECOVERY_PHONE = env("SYSTEM_ADMIN_RECOVERY_PHONE")

# Storage Configuration
STORAGE_BACKEND = env("STORAGE_BACKEND")
STORAGE_DIR = MEDIA_ROOT

# Payment & SMS Provider Abstraction
PAYMENT_PROVIDER_BACKEND = env("PAYMENT_PROVIDER_BACKEND")
SMS_PROVIDER_BACKEND = env("SMS_PROVIDER_BACKEND")

# Church Merchant Configuration
CHURCH_MERCHANT_NUMBERS = env("CHURCH_MERCHANT_NUMBERS")
PAYMENT_WEBHOOK_SECRET = env("PAYMENT_WEBHOOK_SECRET")
RECEIPT_ACCESS_TOKEN_MAX_AGE = env.int("RECEIPT_ACCESS_TOKEN_MAX_AGE", default=86400)
