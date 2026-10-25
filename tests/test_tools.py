"""
Tests for the MCP retail tool server (invoked directly, not via stdio).
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# Import the server module's tool functions directly for unit testing
import importlib

retail = importlib.import_module("mcp_servers.retail_server")


class TestLookUpOrder:
    def test_found(self):
        result = retail.look_up_order("ORD-001")
        assert result["item"] == "Running Shoes (Size 10)"
        assert result["status"] == "delivered"

    def test_not_found(self):
        result = retail.look_up_order("ORD-999")
        assert "error" in result

    def test_case_insensitive(self):
        result = retail.look_up_order("ord-001")
        assert "error" not in result


class TestCheckReturnEligibility:
    def test_eligible(self):
        result = retail.check_return_eligibility("ORD-001")
        assert result["eligible"] is True
        assert "max_refund" in result

    def test_expired_window(self):
        result = retail.check_return_eligibility("ORD-003")  # 40 days old
        assert result["eligible"] is False
        assert "expired" in result["reason"].lower()

    def test_not_delivered(self):
        result = retail.check_return_eligibility("ORD-002")  # pending
        assert result["eligible"] is False


class TestCancelOrder:
    def test_pending_order_returns_approval_payload(self):
        result = retail.cancel_order("ORD-002", "Changed my mind")
        assert result.get("__requires_approval__") is True
        assert result["action"] == "cancel_order"

    def test_delivered_cannot_cancel(self):
        result = retail.cancel_order("ORD-001", "want to cancel")
        assert "error" in result
        assert "PENDING" in result["error"]


class TestProcessRefund:
    def test_valid_refund_returns_approval_payload(self):
        result = retail.process_refund("ORD-001", 50.00, "Doesn't fit")
        assert result.get("__requires_approval__") is True
        assert result["refund_amount"] == 50.00

    def test_overpayment_blocked(self):
        result = retail.process_refund("ORD-001", 200.00, "over-claim")
        assert "error" in result
        assert "exceed" in result["error"]

    def test_expired_window(self):
        result = retail.process_refund("ORD-003", 30.00, "late return")
        assert "error" in result
        assert "expired" in result["error"]
