import importlib
from django.conf import settings
from apps.notifications.providers.base import SmsProvider

# Singleton instance cache for testing/development reuse if desired
_DEFAULT_SMS_PROVIDER = None


def get_sms_provider(new_instance: bool = False) -> SmsProvider:
    """
    Returns the configured SmsProvider instance.
    Swappable via settings.SMS_PROVIDER_BACKEND.
    """
    global _DEFAULT_SMS_PROVIDER
    if _DEFAULT_SMS_PROVIDER is not None and not new_instance:
        return _DEFAULT_SMS_PROVIDER

    backend_path = getattr(
        settings,
        "SMS_PROVIDER_BACKEND",
        "apps.notifications.providers.mock.MockSmsProvider",
    )
    module_name, class_name = backend_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    provider_class = getattr(module, class_name)
    instance = provider_class()
    if not new_instance:
        _DEFAULT_SMS_PROVIDER = instance
    return instance


def set_sms_provider_instance(instance: SmsProvider | None) -> None:
    """Sets or clears the active singleton SMS provider instance (useful for test fixtures)."""
    global _DEFAULT_SMS_PROVIDER
    _DEFAULT_SMS_PROVIDER = instance
