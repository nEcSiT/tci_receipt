import importlib
from django.conf import settings
from apps.contributions.providers.base import PaymentProvider


def get_payment_provider() -> PaymentProvider:
    """
    Instantiates and returns the configured PaymentProvider according to settings.
    Ensures provider implementation is completely swappable and credentials/backends
    are never hard-coded in business logic.
    """
    backend_path = getattr(
        settings,
        "PAYMENT_PROVIDER_BACKEND",
        "apps.contributions.providers.mock.MockPaymentProvider",
    )
    module_name, class_name = backend_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    provider_class = getattr(module, class_name)
    return provider_class()
