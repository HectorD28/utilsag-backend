import re
from dataclasses import dataclass

_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?51[\s-]?)?9\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)")
_DNI_CONTEXT_RE = re.compile(
    r"\b(?:DNI|dni|documento(?:\s+de\s+identidad)?|doc\.?)\s*[:#-]?\s*\d{8}\b"
)
_CARD_RE = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")

_MASK_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("EMAIL", _EMAIL_RE, "[EMAIL_REDACTED]"),
    ("PHONE", _PHONE_RE, "[PHONE_REDACTED]"),
    ("DNI", _DNI_CONTEXT_RE, "[DNI_REDACTED]"),
    ("CARD", _CARD_RE, "[CARD_REDACTED]"),
)


@dataclass(frozen=True)
class MaskingResult:
    masked_text: str
    redactions: list[str]


def mask_pii(text: str) -> MaskingResult:
    """Enmascara datos personales sensibles (Ley N.° 29733) sin alterar códigos SBN de 12 dígitos."""
    masked = text
    redactions: list[str] = []

    for label, pattern, replacement in _MASK_RULES:
        masked, count = pattern.subn(replacement, masked)
        if count > 0:
            redactions.append(f"{label}:{count}")

    return MaskingResult(masked_text=masked, redactions=redactions)

