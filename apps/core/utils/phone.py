import re
from typing import List, Optional


def clean_phone_number(raw_phone: Optional[str]) -> str:
    """
    Strips formatting characters (spaces, dashes, parentheses, dots).
    Preserves digits and optional leading plus sign.
    """
    if not raw_phone:
        return ""
    stripped = raw_phone.strip()
    has_plus = stripped.startswith("+")
    digits = re.sub(r"\D", "", stripped)
    return f"+{digits}" if has_plus else digits


def get_phone_lookup_variants(phone: Optional[str]) -> List[str]:
    """
    Generates equivalent phone number variants for robust matching across
    local (024XXXXXXX) and international (+23324XXXXXXX / 23324XXXXXXX / 0023324XXXXXXX) formats.

    Ghanaian mobile numbers consist of 9 subscriber digits (e.g. 24XXXXXXX).
    Standard local format has leading 0 (10 digits).
    International format has country code 233 (+23324XXXXXXX or 23324XXXXXXX).
    """
    if not phone:
        return []

    raw = phone.strip()
    cleaned = clean_phone_number(raw)
    digits_only = re.sub(r"\D", "", raw)

    variants = []
    for candidate in [raw, cleaned, digits_only]:
        if candidate and candidate not in variants:
            variants.append(candidate)

    if not digits_only:
        return variants

    # Detect Ghana mobile format:
    # Case 1: Local format starting with 0, 10 digits total (e.g. 0241234567)
    if len(digits_only) == 10 and digits_only.startswith("0"):
        subscriber = digits_only[1:]  # 9 digits
        for v in [
            f"0{subscriber}",
            f"+233{subscriber}",
            f"233{subscriber}",
            f"00233{subscriber}",
        ]:
            if v not in variants:
                variants.append(v)

    # Case 2: International format starting with 233, 12 digits total (e.g. 233241234567)
    elif len(digits_only) == 12 and digits_only.startswith("233"):
        subscriber = digits_only[3:]  # 9 digits
        for v in [
            f"0{subscriber}",
            f"+233{subscriber}",
            f"233{subscriber}",
            f"00233{subscriber}",
        ]:
            if v not in variants:
                variants.append(v)

    # Case 3: International format with 00233, 14 digits total (e.g. 00233241234567)
    elif len(digits_only) == 14 and digits_only.startswith("00233"):
        subscriber = digits_only[5:]  # 9 digits
        for v in [
            f"0{subscriber}",
            f"+233{subscriber}",
            f"233{subscriber}",
            f"00233{subscriber}",
        ]:
            if v not in variants:
                variants.append(v)

    # General international number with '+': also add version without '+' and with '00'
    if cleaned.startswith("+"):
        num_without_plus = cleaned[1:]
        if num_without_plus not in variants:
            variants.append(num_without_plus)
        double_zero = f"00{num_without_plus}"
        if double_zero not in variants:
            variants.append(double_zero)
    elif digits_only.startswith("00"):
        num_without_00 = digits_only[2:]
        with_plus = f"+{num_without_00}"
        if with_plus not in variants:
            variants.append(with_plus)
        if num_without_00 not in variants:
            variants.append(num_without_00)
    else:
        with_plus = f"+{digits_only}"
        if with_plus not in variants:
            variants.append(with_plus)

    return variants


def extract_first_name_from_provider_name(provider_name: Optional[str]) -> Optional[str]:
    """
    Extracts a usable first name from the provider-supplied payer name.
    If a full name is supplied, takes the first name token without inventing a name.
    If no usable alphabetic name is supplied, returns None.
    """
    if not provider_name:
        return None

    cleaned = provider_name.strip()
    if not cleaned:
        return None

    tokens = cleaned.split()
    if not tokens:
        return None

    # Find the first token that contains at least one alphabetic character
    for token in tokens:
        clean_token = token.strip(" ,.-_/\\\"'()[]{}")
        if any(c.isalpha() for c in clean_token) and len(clean_token) >= 2:
            return clean_token.capitalize()

    return None


def validate_phone_number(phone: Optional[str]) -> bool:
    """
    Validates that a phone number contains a valid format:
    optional leading '+', followed by 9 to 15 digits.
    """
    if not phone:
        return False
    cleaned = clean_phone_number(phone)
    digits = re.sub(r"\D", "", cleaned)
    if not (9 <= len(digits) <= 15):
        return False
    return bool(re.match(r"^\+?[0-9]{9,15}$", cleaned))


def normalize_phone_number(phone: Optional[str]) -> str:
    """
    Standardizes a phone number by stripping whitespace and non-digit formatting characters.
    Preserves digits and optional leading plus sign.
    """
    return clean_phone_number(phone)
