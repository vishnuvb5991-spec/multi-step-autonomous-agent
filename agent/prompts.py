"""
System prompt provider for customer-support domains.

Prompts and domain policy rules can be externalized and configured via:
  1. Environment variables (RETAIL_SYSTEM_PROMPT, AIRLINE_SYSTEM_PROMPT, TELECOM_SYSTEM_PROMPT)
  2. External template files in `config/prompts/<domain>.txt`
  3. Safe default fallback templates
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Base instructions for customer support
_DEFAULT_BASE = """You are a helpful customer support agent. Follow these rules:
- Always verify the customer's situation before taking any action.
- Never make promises that violate the domain policy.
- If a task cannot be completed within policy, explain clearly and offer alternatives.
- For irreversible actions (cancellations, refunds, rebooking), confirm the details with the customer before calling the tool.
- If you cannot resolve the issue after {max_steps} tool calls, escalate to a human supervisor.
- Keep responses concise and professional.
"""

_DEFAULT_RETAIL = (
    _DEFAULT_BASE
    + """
Domain: Retail / E-commerce

Policy rules:
- Refunds are allowed within 30 days of delivery.
- Refund amount cannot exceed the original purchase price.
- Only PENDING orders may be cancelled; shipped/delivered orders cannot be cancelled.
- Exchanges require the original item to be returned first.
- If the customer is unhappy with a policy decision, escalate rather than override.

Available tools: look_up_order, cancel_order, process_refund, check_return_eligibility, get_exchange_options
"""
)

_DEFAULT_AIRLINE = (
    _DEFAULT_BASE
    + """
Domain: Airline / Travel Support

Policy rules:
- Flights may be cancelled penalty-free within 24 hours of booking.
- After 24 hours, cancellation fees apply (Economy: $200, Business: $100, First: waived).
- Rebooking is allowed up to 2 hours before departure.
- Seat upgrades are subject to availability and customer tier.
- Vouchers are issued for delays > 3 hours caused by the airline.

Available tools: get_booking, cancel_flight, rebook_flight, change_seat, apply_voucher, check_flight_status
"""
)

_DEFAULT_TELECOM = (
    _DEFAULT_BASE
    + """
Domain: Telecom / Technical Support

Policy rules:
- Always run a remote diagnostic before dispatching a technician.
- Refresh the network signal before asking the customer to restart their device.
- Credit for outages > 4 hours is automatic (1 day bill credit per 4-hour block).
- Escalate hardware issues beyond software troubleshooting to Level 2 support.

Available tools: get_account, check_network_status, refresh_network_signal, apply_bill_credit, escalate_ticket
"""
)

_DEFAULT_PROMPTS = {
    "retail": _DEFAULT_RETAIL,
    "airline": _DEFAULT_AIRLINE,
    "telecom": _DEFAULT_TELECOM,
}


def _load_prompt_from_file(domain: str) -> str | None:
    """Attempt to load custom prompt from external config file if provided."""
    prompt_dir = os.getenv("PROMPT_CONFIG_DIR", "config/prompts")
    prompt_file = Path(prompt_dir) / f"{domain}.txt"
    if prompt_file.is_file():
        try:
            return prompt_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not read prompt file %s: %s", prompt_file, exc)
    return None


def get_system_prompt(domain: str, max_steps: int = 10) -> str:
    """Return the system prompt for the given domain.

    Resolution order:
      1. Environment variable (e.g. RETAIL_SYSTEM_PROMPT)
      2. External template file (`config/prompts/<domain>.txt`)
      3. Built-in default template
    """
    domain = domain.lower()
    env_var_name = f"{domain.upper()}_SYSTEM_PROMPT"

    # 1. Environment variable override
    env_prompt = os.getenv(env_var_name)
    if env_prompt and env_prompt.strip():
        return env_prompt.strip().format(max_steps=max_steps)

    # 2. File-based template override
    file_prompt = _load_prompt_from_file(domain)
    if file_prompt and file_prompt.strip():
        return file_prompt.strip().format(max_steps=max_steps)

    # 3. Default fallback
    prompt = _DEFAULT_PROMPTS.get(domain, _DEFAULT_BASE)
    return prompt.format(max_steps=max_steps)
