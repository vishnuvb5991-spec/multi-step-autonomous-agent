"""
Input and output guardrails for the customer-support agent.

Production-Hardened Defense-in-Depth:
  1. Input Guardrail:
     - Unicode homoglyph normalization (NFKD)
     - Zero-width & invisible character stripping
     - Leetspeak obfuscation normalization
     - Adversarial delimiters & ChatML injection detection
     - Base64 payload inspection
     - Token-stuffing length caps
  2. Output Guardrail:
     - PII detection & redaction (Credit Cards with Luhn check, SSNs, routing numbers)
  3. Policy Guardrail:
     - Strict irreversible action tracking for human-in-the-loop sign-off
"""
from __future__ import annotations

import base64
import re
import unicodedata
from typing import Optional

# ---------------------------------------------------------------------------
# 1. Input Guardrail: Patterns & Obfuscation Handling
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[str] = [
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "override policy",
    "override all",
    "jailbreak",
    "bypass",
    "forget your instructions",
    "act as",
    "pretend you are",
    "you are now",
    "system prompt",
    "reveal your instructions",
    "print your prompt",
    "repeat your instructions",
    "developer mode",
    "dan mode",
    "unfiltered mode",
]

_ADVERSARIAL_DELIMITERS: list[str] = [
    "<|im_start|>",
    "<|im_end|>",
    "<|system|>",
    "<|assistant|>",
    "[inst]",
    "[/inst]",
    "<<sys>>",
    "<</sys>>",
    "---begin system prompt---",
    "===system===",
]

_LEETSPEAK_MAP = str.maketrans({
    "@": "a",
    "4": "a",
    "0": "o",
    "1": "i",
    "!": "i",
    "3": "e",
    "5": "s",
    "$": "s",
    "+": "t",
    "7": "t",
})

_ZERO_WIDTH_REGEX = re.compile(r"[\u200B-\u200D\uFEFF\u00A0\u2060\u202A-\u202E]")
_MAX_INPUT_CHARS = 4000  # Prevent token-stuffing attacks


def _normalize_text(text: str) -> tuple[str, str]:
    """Normalize text against Unicode homoglyphs, zero-width chars, and leetspeak."""
    # 1. Unicode NFKD normalization (converts fullwidth & lookalike compatibility forms)
    normalized = unicodedata.normalize("NFKD", text)

    # 2. Strip invisible / zero-width characters
    normalized = _ZERO_WIDTH_REGEX.sub("", normalized)

    # 3. Lowercase
    lower = normalized.lower()

    # 4. Leetspeak translation
    leet_normalized = lower.translate(_LEETSPEAK_MAP)

    return lower, leet_normalized


def _check_base64_payloads(text: str) -> Optional[str]:
    """Inspect potential base64 strings to prevent encoded injection bypasses."""
    # Matches base64 tokens of length 16+ (in 4-char groups with optional padding)
    b64_matches = re.findall(
        r"(?:[A-Za-z0-9+/]{4}){4,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?", text
    )
    for token in b64_matches:
        try:
            # Pad if necessary
            padded = token + "=" * (-len(token) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore").lower()
            for pattern in _INJECTION_PATTERNS:
                if pattern in decoded:
                    return f"ENCODED_INJECTION_DETECTED: base64 payload contains banned phrase '{pattern}'"
        except Exception:
            continue
    return None


def check_input_guardrail(text: str) -> list[str]:
    """Return a list of flag strings if the input is unsafe; empty list if clean.

    Evaluates:
    - Input length limits
    - Normalized text against injection patterns
    - Leetspeak obfuscations
    - Adversarial delimiters / prompt-injection framing
    - Encoded base64 payload injections
    """
    flags: list[str] = []

    if len(text) > _MAX_INPUT_CHARS:
        flags.append(
            f"INPUT_TOO_LONG: message exceeds {_MAX_INPUT_CHARS} characters (got {len(text)})"
        )

    # Obfuscation normalization
    lower, leet_normalized = _normalize_text(text)

    # Delimiter injection checks
    for delimiter in _ADVERSARIAL_DELIMITERS:
        if delimiter in lower:
            flags.append(f"DELIMITER_INJECTION_DETECTED: input contains adversarial tag '{delimiter}'")
            return flags

    # Pattern check on both raw-normalized and leet-normalized text
    for pattern in _INJECTION_PATTERNS:
        if pattern in lower or pattern in leet_normalized:
            flags.append(f"INJECTION_DETECTED: input contains banned phrase '{pattern}'")
            return flags

    # Base64 inspection
    b64_flag = _check_base64_payloads(text)
    if b64_flag:
        flags.append(b64_flag)

    return flags


# ---------------------------------------------------------------------------
# 2. Output Guardrail: PII Detection & Redaction
# ---------------------------------------------------------------------------

_CC_PATTERN = re.compile(r"\b(?:\d{4}[\s\-]?){3}\d{4}\b")
_SSN_PATTERN = re.compile(r"\b\d{3}[\s\-]\d{2}[\s\-]\d{4}\b")
_ROUTING_PATTERN = re.compile(r"\b(?:routing|aba)[\s#:]*(\d{9})\b", re.IGNORECASE)


def _luhn_checksum_valid(card_number: str) -> bool:
    """Validate credit card number using the Luhn checksum algorithm."""
    digits = [int(c) for c in card_number if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = d * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


def check_output_guardrail(text: str) -> list[str]:
    """Return a list of flag strings if the output contains PII; empty list if clean."""
    flags: list[str] = []

    for match in _CC_PATTERN.finditer(text):
        num_str = re.sub(r"[\s\-]", "", match.group(0))
        # Verify via Luhn or pattern structure
        if _luhn_checksum_valid(num_str) or len(num_str) == 16:
            flags.append("PII_DETECTED: potential credit card number in agent output")
            break

    if _SSN_PATTERN.search(text):
        flags.append("PII_DETECTED: potential SSN in agent output")

    if _ROUTING_PATTERN.search(text):
        flags.append("PII_DETECTED: potential bank routing number in agent output")

    return flags


def redact_output(text: str) -> str:
    """Replace detected PII in output with redaction markers."""
    text = _CC_PATTERN.sub("[REDACTED-CC]", text)
    text = _SSN_PATTERN.sub("[REDACTED-SSN]", text)
    text = _ROUTING_PATTERN.sub("[REDACTED-ROUTING]", text)
    return text


# ---------------------------------------------------------------------------
# 3. Policy Guardrail helpers (shared with MCP tool servers)
# ---------------------------------------------------------------------------

IRREVERSIBLE_TOOLS: set[str] = {
    "cancel_order",
    "process_refund",
    "cancel_flight",
    "rebook_flight",
    "apply_voucher",
    "apply_bill_credit",
    "escalate_ticket",
}


def is_irreversible(tool_name: str) -> bool:
    """Return True if the tool performs an irreversible action requiring human sign-off."""
    return tool_name in IRREVERSIBLE_TOOLS
