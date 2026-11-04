from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SmsResult:
    """
    Normalized result of an SMS dispatch operation.
    """
    success: bool
    provider_reference: Optional[str] = None
    status: str = "SENT"  # "SENT", "DELIVERED", "FAILED"
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None
    provider_response: Dict[str, Any] = field(default_factory=dict)


class SmsProvider(ABC):
    """
    Abstract interface for SMS notification providers (e.g., Hubtel, Twilio, Arkesel).
    Decouples SMS dispatch from business logic.
    """

    @property
    @abstractmethod
    def provider_code(self) -> str:
        pass

    @abstractmethod
    def send_sms(self, recipient_phone: str, message: str) -> SmsResult:
        """
        Dispatches an SMS message to the specified phone number.
        Returns normalized SmsResult.
        """
        pass
