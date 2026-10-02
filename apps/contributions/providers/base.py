from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional
from django.db import models


class PaymentStatus(models.TextChoices):
    SUCCESSFUL = "SUCCESSFUL", "Successful"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"
    PENDING = "PENDING", "Pending"


@dataclass
class PaymentResult:
    """
    Normalized result returned from payment provider operations.
    Abstracts provider-specific transaction representations.
    """
    provider: str
    provider_reference: str
    transaction_phone: str
    amount: Decimal
    currency: str = "GHS"
    status: str = PaymentStatus.SUCCESSFUL
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None
    provider_name: Optional[str] = None
    provider_data: Dict[str, Any] = field(default_factory=dict)
    raw_payload: Optional[Dict[str, Any]] = None

    @property
    def is_successful(self) -> bool:
        return self.status == PaymentStatus.SUCCESSFUL

    @property
    def is_failed(self) -> bool:
        return self.status == PaymentStatus.FAILED

    @property
    def is_cancelled(self) -> bool:
        return self.status == PaymentStatus.CANCELLED

    @property
    def is_pending(self) -> bool:
        return self.status == PaymentStatus.PENDING


class PaymentProvider(ABC):
    """
    Abstract interface for inbound/outbound digital payment providers
    (e.g., MTN Mobile Money, Telecel Cash, Paystack, Hubtel).
    """

    @property
    @abstractmethod
    def provider_code(self) -> str:
        """Unique provider code, e.g. MTN_MOMO, PAYSTACK."""
        pass

    @abstractmethod
    def verify_payment(self, reference: str) -> PaymentResult:
        """
        Queries the provider to verify the current status of a payment.
        """
        pass

    @abstractmethod
    def parse_webhook(self, payload: Dict[str, Any]) -> PaymentResult:
        """
        Parses provider webhook callback payload into normalized PaymentResult.
        """
        pass

    @abstractmethod
    def initiate_payment(
        self,
        phone: str,
        amount: Decimal,
        description: str,
        reference: Optional[str] = None,
    ) -> PaymentResult:
        """
        Initiates a USSD push / prompt to contributor's mobile device.
        """
        pass
