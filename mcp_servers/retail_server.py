"""
Retail MCP Tool Server
======================
Exposes customer-support tools for an e-commerce domain.
Run standalone:  python mcp_servers/retail_server.py

Tools:
  - look_up_order
  - check_return_eligibility
  - get_exchange_options
  - cancel_order            (IRREVERSIBLE — triggers human sign-off)
  - process_refund          (IRREVERSIBLE — triggers human sign-off)
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
try:
    from fastmcp import FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("RetailServer")

# ---------------------------------------------------------------------------
# In-memory order database (replace with real DB in production)
# ---------------------------------------------------------------------------

_ORDERS: dict[str, dict] = {
    "ORD-001": {
        "status": "delivered",
        "item": "Running Shoes (Size 10)",
        "price": 89.99,
        "currency": "USD",
        "delivered_at": (datetime.now() - timedelta(days=5)).isoformat(),
        "days_since_delivery": 5,
        "customer_id": "CUST-101",
    },
    "ORD-002": {
        "status": "pending",
        "item": "Wireless Headphones",
        "price": 149.99,
        "currency": "USD",
        "delivered_at": None,
        "days_since_delivery": None,
        "customer_id": "CUST-102",
    },
    "ORD-003": {
        "status": "delivered",
        "item": "Coffee Maker",
        "price": 59.99,
        "currency": "USD",
        "delivered_at": (datetime.now() - timedelta(days=40)).isoformat(),
        "days_since_delivery": 40,
        "customer_id": "CUST-103",
    },
    "ORD-004": {
        "status": "shipped",
        "item": "Laptop Stand",
        "price": 39.99,
        "currency": "USD",
        "delivered_at": None,
        "days_since_delivery": None,
        "customer_id": "CUST-104",
    },
}

# Track which orders have been processed (mutation)
_PROCESSED: dict[str, str] = {}  # order_id -> "cancelled" | "refunded"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def look_up_order(order_id: str) -> dict:
    """Look up an order by its order ID.

    Returns order details including status, item name, price, and delivery information.
    Use this first to understand the order before taking any action.

    Args:
        order_id: The order ID (e.g. "ORD-001").

    Returns:
        A dict with order details, or an error dict if not found.
    """
    order = _ORDERS.get(order_id.upper())
    if not order:
        return {"error": f"Order '{order_id}' not found. Please verify the order ID."}

    result = dict(order)
    if order_id in _PROCESSED:
        result["processed_as"] = _PROCESSED[order_id]
    return result


@mcp.tool()
def check_return_eligibility(order_id: str) -> dict:
    """Check if an order is eligible for a return or refund.

    Policy: returns accepted within 30 days of delivery for delivered orders only.

    Args:
        order_id: The order ID to check.

    Returns:
        A dict with eligibility status and reason.
    """
    order = _ORDERS.get(order_id.upper())
    if not order:
        return {"error": f"Order '{order_id}' not found."}

    if order["status"] != "delivered":
        return {
            "eligible": False,
            "reason": f"Order status is '{order['status']}'. Only delivered orders are eligible for returns.",
        }

    days = order.get("days_since_delivery", 0) or 0
    if days > 30:
        return {
            "eligible": False,
            "reason": f"Return window expired. Order was delivered {days} days ago (policy: ≤30 days).",
        }

    if order_id in _PROCESSED:
        return {"eligible": False, "reason": f"Order already {_PROCESSED[order_id]}."}

    return {
        "eligible": True,
        "reason": f"Order eligible for return ({days} days since delivery, within the 30-day window).",
        "max_refund": order["price"],
    }


@mcp.tool()
def get_exchange_options(order_id: str) -> dict:
    """Get available exchange options for an eligible order.

    Args:
        order_id: The order ID to check.

    Returns:
        A dict with available exchange variants.
    """
    order = _ORDERS.get(order_id.upper())
    if not order:
        return {"error": f"Order '{order_id}' not found."}

    # Simulate exchange options based on item category
    item = order["item"]
    return {
        "order_id": order_id,
        "original_item": item,
        "exchange_options": [
            {"variant": f"{item} - Different Color", "price_delta": 0.0},
            {"variant": f"{item} - Different Size", "price_delta": 0.0},
            {"variant": "Store Credit", "amount": order["price"]},
        ],
        "note": "Customer must return the original item before the exchange ships.",
    }


@mcp.tool()
def cancel_order(order_id: str, reason: str) -> dict:
    """Cancel an order that has not yet shipped.

    ⚠️  IRREVERSIBLE ACTION — this tool will trigger a human sign-off request.

    Policy: Only PENDING orders may be cancelled. Shipped or delivered orders cannot be cancelled.

    Args:
        order_id: The order ID to cancel.
        reason: The customer-provided reason for cancellation.

    Returns:
        A confirmation dict, or an error dict if cancellation is not possible.
    """
    order = _ORDERS.get(order_id.upper())
    if not order:
        return {"error": f"Order '{order_id}' not found."}

    if order["status"] != "pending":
        return {
            "error": (
                f"Cannot cancel order with status '{order['status']}'. "
                "Only PENDING orders are eligible for cancellation."
            )
        }

    if order_id in _PROCESSED:
        return {"error": f"Order has already been {_PROCESSED[order_id]}."}

    # Mark as irreversible — LangGraph will interrupt for human approval
    return {
        "__requires_approval__": True,
        "action": "cancel_order",
        "order_id": order_id,
        "item": order["item"],
        "price": order["price"],
        "reason": reason,
        "message": f"Cancelling order {order_id} ({order['item']}, ${order['price']}). "
                   "This action is irreversible.",
    }


def _execute_cancel_order(order_id: str) -> dict:
    """Actually perform the cancellation after human approval."""
    order = _ORDERS.get(order_id.upper())
    if not order:
        return {"error": "Order not found during execution."}
    order["status"] = "cancelled"
    _PROCESSED[order_id] = "cancelled"
    return {
        "success": True,
        "order_id": order_id,
        "message": f"Order {order_id} has been successfully cancelled. A confirmation email will be sent.",
    }


@mcp.tool()
def process_refund(order_id: str, amount: float, reason: str) -> dict:
    """Process a refund for a delivered order.

    ⚠️  IRREVERSIBLE ACTION — this tool will trigger a human sign-off request.

    Policy:
    - Order must have been delivered within the last 30 days.
    - Refund amount cannot exceed the original purchase price.

    Args:
        order_id: The order ID to refund.
        amount: The refund amount in USD (must be ≤ original price).
        reason: The reason for the refund.

    Returns:
        A confirmation dict, or a policy-violation error dict.
    """
    order = _ORDERS.get(order_id.upper())
    if not order:
        return {"error": f"Order '{order_id}' not found."}

    if order["status"] != "delivered":
        return {"error": f"Refunds only apply to delivered orders (current status: '{order['status']}')."}

    days = order.get("days_since_delivery", 0) or 0
    if days > 30:
        return {"error": f"Refund window expired ({days} days since delivery; policy ≤ 30 days)."}

    if amount > order["price"]:
        return {
            "error": (
                f"Refund amount ${amount:.2f} exceeds original price ${order['price']:.2f}. "
                "Maximum refund is the original purchase price."
            )
        }

    if amount <= 0:
        return {"error": "Refund amount must be greater than $0."}

    if order_id in _PROCESSED:
        return {"error": f"Order has already been {_PROCESSED[order_id]}."}

    # Mark as irreversible — LangGraph will interrupt for human approval
    return {
        "__requires_approval__": True,
        "action": "process_refund",
        "order_id": order_id,
        "item": order["item"],
        "refund_amount": amount,
        "original_price": order["price"],
        "reason": reason,
        "message": f"Refunding ${amount:.2f} for order {order_id} ({order['item']}). "
                   "This action is irreversible.",
    }


def _execute_process_refund(order_id: str, amount: float) -> dict:
    """Actually perform the refund after human approval."""
    order = _ORDERS.get(order_id.upper())
    if not order:
        return {"error": "Order not found during execution."}
    _PROCESSED[order_id] = "refunded"
    return {
        "success": True,
        "order_id": order_id,
        "refund_amount": amount,
        "message": (
            f"Refund of ${amount:.2f} for order {order_id} has been processed. "
            "Funds will appear within 3-5 business days."
        ),
    }


# Expose execution helpers so agent/graph.py can call them after approval
EXECUTION_HANDLERS: dict[str, callable] = {
    "cancel_order": _execute_cancel_order,
    "process_refund": _execute_process_refund,
}


if __name__ == "__main__":
    mcp.run(transport="stdio")
