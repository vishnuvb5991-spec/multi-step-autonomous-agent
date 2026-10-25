"""
Telecom MCP Tool Server
=======================
Exposes customer-support tools for a telecom / technical-support domain.
Run standalone:  python mcp_servers/telecom_server.py

Tools:
  - get_account
  - check_network_status
  - refresh_network_signal
  - apply_bill_credit       (IRREVERSIBLE — triggers human sign-off)
  - escalate_ticket         (IRREVERSIBLE — triggers human sign-off)
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
from datetime import datetime, timedelta
try:
    from fastmcp import FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("TelecomServer")

# ---------------------------------------------------------------------------
# In-memory account / network database
# ---------------------------------------------------------------------------

_ACCOUNTS: dict[str, dict] = {
    "ACC-5001": {
        "customer": "David Lee",
        "plan": "Premium Unlimited",
        "monthly_bill": 85.00,
        "status": "active",
        "device": "iPhone 15 Pro",
        "area_code": "415",
        "open_tickets": [],
    },
    "ACC-5002": {
        "customer": "Eva Martinez",
        "plan": "Basic 5G",
        "monthly_bill": 45.00,
        "status": "active",
        "device": "Samsung Galaxy S24",
        "area_code": "628",
        "open_tickets": ["TKT-2201"],
    },
    "ACC-5003": {
        "customer": "Frank Chen",
        "plan": "Family Share",
        "monthly_bill": 120.00,
        "status": "suspended",
        "device": "Google Pixel 9",
        "area_code": "510",
        "open_tickets": [],
    },
}

_NETWORK_STATUS: dict[str, dict] = {
    "415": {
        "signal": "degraded",
        "outage_start": (datetime.now() - timedelta(hours=6)).isoformat(),
        "outage_hours": 6,
        "cause": "Fiber maintenance",
        "estimated_resolution": (datetime.now() + timedelta(hours=2)).isoformat(),
    },
    "628": {
        "signal": "normal",
        "outage_start": None,
        "outage_hours": 0,
        "cause": None,
        "estimated_resolution": None,
    },
    "510": {
        "signal": "outage",
        "outage_start": (datetime.now() - timedelta(hours=10)).isoformat(),
        "outage_hours": 10,
        "cause": "Tower hardware failure",
        "estimated_resolution": (datetime.now() + timedelta(hours=6)).isoformat(),
    },
}

_CREDITS_ISSUED: dict[str, list] = {}  # account_id -> list of credit amounts
_ESCALATED: set[str] = set()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_account(account_id: str) -> dict:
    """Retrieve a customer account by its account ID.

    Args:
        account_id: The account ID (e.g. "ACC-5001").

    Returns:
        Account details including plan, device, status, and open tickets.
    """
    account = _ACCOUNTS.get(account_id.upper())
    if not account:
        return {"error": f"Account '{account_id}' not found."}
    result = dict(account)
    if account_id in _CREDITS_ISSUED:
        result["credits_issued_this_session"] = _CREDITS_ISSUED[account_id]
    return result


@mcp.tool()
def check_network_status(account_id: str) -> dict:
    """Check the network signal and outage status for a customer's area.

    Args:
        account_id: The account ID — used to look up the customer's area code.

    Returns:
        Network status including signal quality, outage duration, and estimated resolution.
    """
    account = _ACCOUNTS.get(account_id.upper())
    if not account:
        return {"error": f"Account '{account_id}' not found."}

    area_code = account["area_code"]
    status = _NETWORK_STATUS.get(area_code, {"signal": "normal", "outage_hours": 0})
    result = dict(status)
    result["area_code"] = area_code
    result["customer"] = account["customer"]

    # Auto-credit eligibility note
    outage_hours = status.get("outage_hours", 0)
    if outage_hours >= 4:
        credits = (outage_hours // 4) * (account["monthly_bill"] / 30)
        result["credit_eligible"] = True
        result["suggested_credit"] = round(credits, 2)
        result["credit_note"] = (
            f"Customer qualifies for a bill credit of ${credits:.2f} "
            f"({outage_hours}h outage ÷ 4h per credit day × daily rate). "
            "Use apply_bill_credit to issue."
        )
    else:
        result["credit_eligible"] = False

    return result


@mcp.tool()
def refresh_network_signal(account_id: str) -> dict:
    """Push a remote network signal refresh to the customer's device.

    This is a non-destructive diagnostic action. Should be tried before
    asking the customer to restart their device or router.

    Args:
        account_id: The account ID.

    Returns:
        Result of the signal refresh attempt.
    """
    account = _ACCOUNTS.get(account_id.upper())
    if not account:
        return {"error": f"Account '{account_id}' not found."}

    if account["status"] == "suspended":
        return {"error": "Cannot refresh signal for a suspended account."}

    area_code = account["area_code"]
    network = _NETWORK_STATUS.get(area_code, {})

    if network.get("signal") == "outage":
        return {
            "success": False,
            "message": (
                "Cannot refresh signal — there is an active network outage in the customer's area. "
                f"Estimated resolution: {network.get('estimated_resolution', 'unknown')}. "
                "Consider issuing a bill credit."
            ),
        }

    # Simulate a refresh (in production, this would call a provisioning API)
    success = random.random() > 0.2  # 80% success
    if success:
        return {
            "success": True,
            "message": (
                f"Network signal refreshed for {account['customer']}'s {account['device']}. "
                "The device may take up to 2 minutes to reconnect. "
                "Ask the customer to check their signal in a moment."
            ),
        }
    return {
        "success": False,
        "message": (
            "Signal refresh did not resolve the issue. "
            "Please ask the customer to restart their device, or consider escalating."
        ),
    }


@mcp.tool()
def apply_bill_credit(account_id: str, credit_amount: float, reason: str) -> dict:
    """Apply a bill credit to a customer's account.

    ⚠️  IRREVERSIBLE ACTION — triggers human sign-off.

    Policy:
    - Credits are issued automatically for outages ≥4 hours (1 day bill credit per 4-hour block).
    - Maximum credit: one full month's bill.

    Args:
        account_id: The account ID.
        credit_amount: The credit amount in USD.
        reason: The reason for the credit (e.g., "6-hour network outage").

    Returns:
        Approval-pending payload, or an error dict.
    """
    account = _ACCOUNTS.get(account_id.upper())
    if not account:
        return {"error": f"Account '{account_id}' not found."}

    if account["status"] == "suspended":
        return {"error": "Cannot apply credit to a suspended account."}

    if credit_amount <= 0:
        return {"error": "Credit amount must be greater than $0."}

    max_credit = account["monthly_bill"]
    if credit_amount > max_credit:
        return {
            "error": (
                f"Credit amount ${credit_amount:.2f} exceeds maximum allowed "
                f"(one month's bill: ${max_credit:.2f})."
            )
        }

    return {
        "__requires_approval__": True,
        "action": "apply_bill_credit",
        "account_id": account_id,
        "customer": account["customer"],
        "credit_amount": credit_amount,
        "monthly_bill": account["monthly_bill"],
        "reason": reason,
        "message": (
            f"Applying ${credit_amount:.2f} credit to {account['customer']}'s account. "
            "This action is irreversible."
        ),
    }


def _execute_apply_bill_credit(account_id: str, credit_amount: float) -> dict:
    if account_id not in _CREDITS_ISSUED:
        _CREDITS_ISSUED[account_id] = []
    _CREDITS_ISSUED[account_id].append(credit_amount)
    return {
        "success": True,
        "account_id": account_id,
        "credit_amount": credit_amount,
        "message": f"${credit_amount:.2f} credit applied. Will appear on next bill statement.",
    }


@mcp.tool()
def escalate_ticket(account_id: str, issue_summary: str, severity: str) -> dict:
    """Escalate an unresolved issue to Level 2 technical support.

    ⚠️  IRREVERSIBLE ACTION — triggers human sign-off.

    Use when:
    - Software troubleshooting steps have been exhausted.
    - A hardware issue is suspected.
    - The customer is experiencing repeated failures.

    Args:
        account_id: The account ID.
        issue_summary: A concise description of the unresolved problem.
        severity: "low" | "medium" | "high" | "critical".

    Returns:
        Approval-pending payload, or an error dict.
    """
    account = _ACCOUNTS.get(account_id.upper())
    if not account:
        return {"error": f"Account '{account_id}' not found."}

    if account_id in _ESCALATED:
        return {"error": "A ticket has already been escalated for this account in this session."}

    valid_severities = {"low", "medium", "high", "critical"}
    if severity.lower() not in valid_severities:
        return {"error": f"Invalid severity '{severity}'. Must be one of: {valid_severities}."}

    return {
        "__requires_approval__": True,
        "action": "escalate_ticket",
        "account_id": account_id,
        "customer": account["customer"],
        "issue_summary": issue_summary,
        "severity": severity.lower(),
        "message": (
            f"Escalating {severity} issue for {account['customer']} to Level 2 support. "
            "This action is irreversible."
        ),
    }


def _execute_escalate_ticket(account_id: str, issue_summary: str, severity: str) -> dict:
    _ESCALATED.add(account_id)
    account = _ACCOUNTS.get(account_id.upper(), {})
    ticket_id = f"TKT-{hash(account_id + issue_summary) % 9000 + 1000}"
    account.get("open_tickets", []).append(ticket_id)
    return {
        "success": True,
        "account_id": account_id,
        "ticket_id": ticket_id,
        "severity": severity,
        "message": (
            f"Ticket {ticket_id} escalated to Level 2 support ({severity} severity). "
            "A technician will contact the customer within 24 hours."
        ),
    }


EXECUTION_HANDLERS: dict[str, callable] = {
    "apply_bill_credit": _execute_apply_bill_credit,
    "escalate_ticket": _execute_escalate_ticket,
}


if __name__ == "__main__":
    mcp.run(transport="stdio")
