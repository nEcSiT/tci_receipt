import uuid
from typing import Any, Dict, List, Optional
from apps.notifications.providers.base import SmsProvider, SmsResult


class MockSmsProvider(SmsProvider):
    """
    Test double for SMS dispatch.
    Tracks all sent messages in memory and provides deterministic failure simulation
    for testing retries and manual escalation.
    """

    def __init__(self, provider_code: str = "MOCK_SMS"):
        self._provider_code = provider_code
        self.sent_messages: List[Dict[str, Any]] = []
        self.should_fail: bool = False
        self.default_failure_reason: str = "Simulated provider gateway network timeout."
        self.failure_countdown_per_phone: Dict[str, int] = {}
        self.failing_phones: set = set()

    @property
    def provider_code(self) -> str:
        return self._provider_code

    def fail_next_n_times_for_phone(self, phone: str, count: int) -> None:
        """Configures the mock to fail the next `count` attempts for a specific phone, then succeed."""
        self.failure_countdown_per_phone[phone] = count

    def set_phone_to_fail(self, phone: str) -> None:
        """Causes all sends to this phone to fail permanently."""
        self.failing_phones.add(phone)

    def clear(self) -> None:
        """Resets mock state."""
        self.sent_messages.clear()
        self.should_fail = False
        self.failure_countdown_per_phone.clear()
        self.failing_phones.clear()

    def send_sms(self, recipient_phone: str, message: str) -> SmsResult:
        # Check countdown failures
        if recipient_phone in self.failure_countdown_per_phone:
            remaining = self.failure_countdown_per_phone[recipient_phone]
            if remaining > 0:
                self.failure_countdown_per_phone[recipient_phone] = remaining - 1
                return SmsResult(
                    success=False,
                    status="FAILED",
                    failure_code="ERR_GATEWAY_TIMEOUT",
                    failure_reason=f"Simulated failure (remaining countdown: {remaining - 1})",
                    provider_response={"status": "FAILED", "code": 504},
                )

        # Check explicit phone failure
        if recipient_phone in self.failing_phones or "FAIL" in recipient_phone.upper():
            return SmsResult(
                success=False,
                status="FAILED",
                failure_code="ERR_DELIVERY_REJECTED",
                failure_reason=self.default_failure_reason,
                provider_response={"status": "FAILED", "code": 500},
            )

        # Check global flag
        if self.should_fail:
            return SmsResult(
                success=False,
                status="FAILED",
                failure_code="ERR_SERVICE_UNAVAILABLE",
                failure_reason=self.default_failure_reason,
                provider_response={"status": "FAILED", "code": 503},
            )

        # Successful send
        reference = f"SMS-{uuid.uuid4().hex[:10].upper()}"
        record = {
            "recipient_phone": recipient_phone,
            "message": message,
            "provider_reference": reference,
            "status": "DELIVERED",
        }
        self.sent_messages.append(record)

        return SmsResult(
            success=True,
            provider_reference=reference,
            status="DELIVERED",
            provider_response={"status": "DELIVERED", "reference": reference},
        )
