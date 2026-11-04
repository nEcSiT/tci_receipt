import uuid
import hashlib
import hmac
from django.conf import settings
from decimal import Decimal
from typing import Any, Dict, Optional
from apps.contributions.providers.base import PaymentProvider, PaymentResult, PaymentStatus


class MockPaymentProvider(PaymentProvider):
    """
    Test double and configurable mock provider for development and testing.
    Allows simulating successful, failed, cancelled, and delayed/pending transactions.
    """

    def __init__(self, provider_code: str = "MTN_MOMO"):
        self._provider_code = provider_code
        self._canned_responses: Dict[str, PaymentResult] = {}
        self._default_status: str = PaymentStatus.SUCCESSFUL
        self._default_failure_code: Optional[str] = None
        self._default_failure_reason: Optional[str] = None

    @property
    def provider_code(self) -> str:
        return self._provider_code

    def register_response(self, reference: str, result: PaymentResult) -> None:
        """Explicitly registers a predefined outcome for a specific transaction reference."""
        self._canned_responses[reference] = result

    def set_default_status(
        self,
        status: str,
        failure_code: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ) -> None:
        """Sets the default status returned for non-registered references."""
        self._default_status = status
        self._default_failure_code = failure_code
        self._default_failure_reason = failure_reason

    def clear(self) -> None:
        """Resets the mock state."""
        self._canned_responses.clear()
        self._default_status = PaymentStatus.SUCCESSFUL
        self._default_failure_code = None
        self._default_failure_reason = None

    def _determine_status_from_reference(
        self,
        reference: str,
        amount: Decimal,
        phone: str,
    ) -> PaymentResult:
        if reference in self._canned_responses:
            return self._canned_responses[reference]

        ref_upper = reference.upper()
        if "FAIL" in ref_upper:
            return PaymentResult(
                provider=self.provider_code,
                provider_reference=reference,
                transaction_phone=phone,
                amount=amount,
                status=PaymentStatus.FAILED,
                failure_code="ERR_INSUFFICIENT_FUNDS",
                failure_reason="Subscriber account has insufficient mobile money balance.",
                provider_data={"raw_status": "FAILED", "code": 4002},
            )
        elif "CANCEL" in ref_upper:
            return PaymentResult(
                provider=self.provider_code,
                provider_reference=reference,
                transaction_phone=phone,
                amount=amount,
                status=PaymentStatus.CANCELLED,
                failure_code="USER_CANCELLED",
                failure_reason="Contributor declined payment authorization prompt on mobile handset.",
                provider_data={"raw_status": "CANCELLED", "code": 4010},
            )
        elif "DELAY" in ref_upper or "PEND" in ref_upper:
            return PaymentResult(
                provider=self.provider_code,
                provider_reference=reference,
                transaction_phone=phone,
                amount=amount,
                status=PaymentStatus.PENDING,
                failure_code="TX_PENDING",
                failure_reason="Awaiting network operator confirmation.",
                provider_data={"raw_status": "PENDING", "code": 1001},
            )

        # Default fallback status
        return PaymentResult(
            provider=self.provider_code,
            provider_reference=reference,
            transaction_phone=phone,
            amount=amount,
            status=self._default_status,
            failure_code=self._default_failure_code,
            failure_reason=self._default_failure_reason,
            provider_data={"raw_status": self._default_status, "mock": True},
        )

    def verify_webhook(self, raw_body: bytes, headers: Dict[str, str]) -> bool:
        secret = getattr(settings, "PAYMENT_WEBHOOK_SECRET", "")
        signature = headers.get("X-Webhook-Signature", "")
        if not secret or not signature:
            return False
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def verify_payment(self, reference: str) -> PaymentResult:
        if reference in self._canned_responses:
            return self._canned_responses[reference]
        return self._determine_status_from_reference(reference, Decimal("100.00"), "0240000001")

    def parse_webhook(self, payload: Dict[str, Any]) -> PaymentResult:
        reference = payload.get("reference") or payload.get("provider_reference") or f"REF-{uuid.uuid4().hex[:8]}"
        phone = payload.get("phone") or payload.get("transaction_phone") or "0240000001"
        amount_raw = payload.get("amount", "100.00")
        amount = Decimal(str(amount_raw))
        status_input = payload.get("status")

        if reference in self._canned_responses:
            return self._canned_responses[reference]

        if status_input:
            status_clean = str(status_input).upper()
            if status_clean in PaymentStatus.values:
                return PaymentResult(
                    provider=payload.get("provider", self.provider_code),
                    provider_reference=reference,
                    transaction_phone=phone,
                    amount=amount,
                    currency=payload.get("currency", "GHS"),
                    status=status_clean,
                    failure_code=payload.get("failure_code"),
                    failure_reason=payload.get("failure_reason"),
                    provider_name=payload.get("provider_name"),
                    provider_data=payload,
                    raw_payload=payload,
                )

        return self._determine_status_from_reference(reference, amount, phone)

    def initiate_payment(
        self,
        phone: str,
        amount: Decimal,
        description: str,
        reference: Optional[str] = None,
    ) -> PaymentResult:
        ref = reference or f"MOMO-{uuid.uuid4().hex[:10].upper()}"
        result = self._determine_status_from_reference(ref, amount, phone)
        result.provider_data["description"] = description
        return result
