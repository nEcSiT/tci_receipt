import secrets
from django.db import transaction
from apps.core.models import BusinessSequence


class IdGenerator:
    """
    Service for generating unique, formatted, concurrency-safe human-readable business IDs.
    Row-counting is strictly avoided.
    """

    @classmethod
    def generate_member_number(cls) -> str:
        """Generates member number format MEM-XXXXXX (e.g., MEM-000012)."""
        val = BusinessSequence.get_next_value("MEMBER_NUMBER", initial_value=1)
        return f"MEM-{val:06d}"

    @classmethod
    def generate_contribution_number(cls) -> str:
        """Generates contribution number format CON-XXXXXXXX (e.g., CON-00001245)."""
        val = BusinessSequence.get_next_value("CONTRIBUTION_NUMBER", initial_value=1)
        return f"CON-{val:08d}"

    @classmethod
    def generate_receipt_number(cls) -> str:
        """
        Generates receipt number format HLC-XXXXXXX with exactly 7 digits.
        Receipt numbers are collision-checked and never reused.
        """
        from apps.receipts.models import Receipt

        for _ in range(50):
            # 7-digit randomized number between 1000000 and 9999999
            num = secrets.randbelow(9000000) + 1000000
            candidate = f"HLC-{num:07d}"
            # Check against all receipts, including soft-deleted ones
            if not Receipt.all_objects.filter(receipt_number=candidate).exists():
                return candidate

        # Fallback to sequential 7-digit if high randomized saturation occurs
        seq_val = BusinessSequence.get_next_value("RECEIPT_NUMBER", initial_value=1000000)
        return f"HLC-{seq_val:07d}"

    @classmethod
    def generate_requisition_number(cls) -> str:
        """Generates requisition number format REQ-XXXXXX (e.g., REQ-000125)."""
        val = BusinessSequence.get_next_value("REQUISITION_NUMBER", initial_value=1)
        return f"REQ-{val:06d}"
