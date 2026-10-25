"""
Airline MCP Tool Server
=======================
Exposes customer-support tools for an airline / travel domain.
Run standalone:  python mcp_servers/airline_server.py

Tools:
  - get_booking
  - check_flight_status
  - change_seat
  - cancel_flight          (IRREVERSIBLE — triggers human sign-off)
  - rebook_flight          (IRREVERSIBLE — triggers human sign-off)
  - apply_voucher          (IRREVERSIBLE — triggers human sign-off)
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

mcp = FastMCP("AirlineServer")

# ---------------------------------------------------------------------------
# In-memory booking database
# ---------------------------------------------------------------------------

_now = datetime.now()

_BOOKINGS: dict[str, dict] = {
    "BK-1001": {
        "passenger": "Alice Johnson",
        "flight": "UA-204",
        "origin": "SFO",
        "destination": "JFK",
        "departure": (_now + timedelta(hours=36)).isoformat(),
        "class": "Economy",
        "seat": "22B",
        "fare_paid": 320.00,
        "booking_age_hours": 20,  # booked 20 hours ago
        "status": "confirmed",
        "tier": "Silver",
    },
    "BK-1002": {
        "passenger": "Bob Smith",
        "flight": "DL-890",
        "origin": "LAX",
        "destination": "ORD",
        "departure": (_now + timedelta(hours=1)).isoformat(),  # <2 hours away
        "class": "Business",
        "seat": "4A",
        "fare_paid": 850.00,
        "booking_age_hours": 48,
        "status": "confirmed",
        "tier": "Gold",
    },
    "BK-1003": {
        "passenger": "Carol White",
        "flight": "AA-512",
        "origin": "BOS",
        "destination": "MIA",
        "departure": (_now - timedelta(hours=5)).isoformat(),  # already departed
        "class": "First",
        "seat": "1A",
        "fare_paid": 1200.00,
        "booking_age_hours": 72,
        "status": "completed",
        "tier": "Platinum",
    },
}

_FLIGHT_STATUS: dict[str, dict] = {
    "UA-204": {"status": "on_time", "gate": "G12", "delay_minutes": 0},
    "DL-890": {"status": "delayed", "gate": "B4", "delay_minutes": 210},  # 3.5 hours
    "AA-512": {"status": "completed", "gate": None, "delay_minutes": 0},
}

_CANCELLATION_FEES = {"Economy": 200.0, "Business": 100.0, "First": 0.0}
_PROCESSED: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_booking(booking_id: str) -> dict:
    """Retrieve a booking by its booking ID.

    Args:
        booking_id: The booking ID (e.g. "BK-1001").

    Returns:
        A dict with full booking details, or an error dict if not found.
    """
    booking = _BOOKINGS.get(booking_id.upper())
    if not booking:
        return {"error": f"Booking '{booking_id}' not found."}
    result = dict(booking)
    if booking_id in _PROCESSED:
        result["processed_as"] = _PROCESSED[booking_id]
    return result


@mcp.tool()
def check_flight_status(flight_number: str) -> dict:
    """Check the current status of a flight.

    Args:
        flight_number: The flight number (e.g. "UA-204").

    Returns:
        A dict with flight status, gate, and delay information.
    """
    status = _FLIGHT_STATUS.get(flight_number.upper())
    if not status:
        return {"error": f"Flight '{flight_number}' not found."}
    result = dict(status)

    # Auto-generate voucher eligibility note for significant delays
    if status["delay_minutes"] >= 180:
        result["voucher_eligible"] = True
        result["voucher_note"] = (
            f"Delay of {status['delay_minutes']} minutes qualifies for a travel voucher. "
            "Use apply_voucher to issue one."
        )
    return result


@mcp.tool()
def change_seat(booking_id: str, preferred_seat: str) -> dict:
    """Change a passenger's seat assignment.

    Policy: Seat changes are allowed up to 2 hours before departure.
    Upgrades require customer tier eligibility.

    Args:
        booking_id: The booking ID.
        preferred_seat: The requested seat (e.g. "14C").

    Returns:
        Confirmation of the seat change, or an error dict.
    """
    booking = _BOOKINGS.get(booking_id.upper())
    if not booking:
        return {"error": f"Booking '{booking_id}' not found."}

    if booking["status"] != "confirmed":
        return {"error": f"Cannot change seat for a '{booking['status']}' booking."}

    departure = datetime.fromisoformat(booking["departure"])
    hours_until = (departure - datetime.now()).total_seconds() / 3600
    if hours_until < 2:
        return {
            "error": (
                f"Cannot change seat — departure is in {hours_until:.1f} hours "
                "(policy: seat changes must be made ≥2 hours before departure)."
            )
        }

    old_seat = booking["seat"]
    booking["seat"] = preferred_seat
    return {
        "success": True,
        "booking_id": booking_id,
        "old_seat": old_seat,
        "new_seat": preferred_seat,
        "message": f"Seat changed from {old_seat} to {preferred_seat} for booking {booking_id}.",
    }


@mcp.tool()
def cancel_flight(booking_id: str, reason: str) -> dict:
    """Cancel a flight booking.

    ⚠️  IRREVERSIBLE ACTION — triggers human sign-off.

    Policy:
    - No fee if booked within the last 24 hours.
    - Economy: $200 fee; Business: $100 fee; First: waived.
    - Cannot cancel completed or already-cancelled bookings.

    Args:
        booking_id: The booking ID.
        reason: The passenger's cancellation reason.

    Returns:
        Approval-pending payload, or an error dict.
    """
    booking = _BOOKINGS.get(booking_id.upper())
    if not booking:
        return {"error": f"Booking '{booking_id}' not found."}

    if booking["status"] in ("completed", "cancelled"):
        return {"error": f"Cannot cancel a '{booking['status']}' booking."}

    if booking_id in _PROCESSED:
        return {"error": f"Booking already {_PROCESSED[booking_id]}."}

    # Determine cancellation fee
    booking_age = booking.get("booking_age_hours", 48)
    cabin_class = booking.get("class", "Economy")
    fee = 0.0 if booking_age <= 24 else _CANCELLATION_FEES.get(cabin_class, 200.0)
    refund = max(0.0, booking["fare_paid"] - fee)

    return {
        "__requires_approval__": True,
        "action": "cancel_flight",
        "booking_id": booking_id,
        "flight": booking["flight"],
        "passenger": booking["passenger"],
        "cancellation_fee": fee,
        "refund_amount": refund,
        "reason": reason,
        "message": (
            f"Cancelling flight {booking['flight']} for {booking['passenger']}. "
            f"Fee: ${fee:.2f}, Refund: ${refund:.2f}. This action is irreversible."
        ),
    }


def _execute_cancel_flight(booking_id: str) -> dict:
    booking = _BOOKINGS.get(booking_id.upper())
    if not booking:
        return {"error": "Booking not found during execution."}
    booking_age = booking.get("booking_age_hours", 48)
    cabin_class = booking.get("class", "Economy")
    fee = 0.0 if booking_age <= 24 else _CANCELLATION_FEES.get(cabin_class, 200.0)
    refund = max(0.0, booking["fare_paid"] - fee)
    booking["status"] = "cancelled"
    _PROCESSED[booking_id] = "cancelled"
    return {
        "success": True,
        "booking_id": booking_id,
        "cancellation_fee": fee,
        "refund_amount": refund,
        "message": f"Booking {booking_id} cancelled. Refund of ${refund:.2f} within 7 business days.",
    }


@mcp.tool()
def rebook_flight(booking_id: str, new_flight: str, new_departure: str) -> dict:
    """Rebook a passenger onto a different flight.

    ⚠️  IRREVERSIBLE ACTION — triggers human sign-off.

    Policy: Rebooking must be requested ≥2 hours before the original departure.

    Args:
        booking_id: The original booking ID.
        new_flight: The new flight number.
        new_departure: ISO-format datetime for the new departure.

    Returns:
        Approval-pending payload, or an error dict.
    """
    booking = _BOOKINGS.get(booking_id.upper())
    if not booking:
        return {"error": f"Booking '{booking_id}' not found."}

    if booking["status"] != "confirmed":
        return {"error": f"Can only rebook confirmed bookings (current: '{booking['status']}')."}

    departure = datetime.fromisoformat(booking["departure"])
    hours_until = (departure - datetime.now()).total_seconds() / 3600
    if hours_until < 2:
        return {
            "error": (
                f"Too late to rebook — departure in {hours_until:.1f} hours "
                "(policy: ≥2 hours required)."
            )
        }

    if booking_id in _PROCESSED:
        return {"error": f"Booking already {_PROCESSED[booking_id]}."}

    return {
        "__requires_approval__": True,
        "action": "rebook_flight",
        "booking_id": booking_id,
        "original_flight": booking["flight"],
        "new_flight": new_flight,
        "new_departure": new_departure,
        "passenger": booking["passenger"],
        "message": (
            f"Rebooking {booking['passenger']} from {booking['flight']} to {new_flight}. "
            "This action is irreversible."
        ),
    }


def _execute_rebook_flight(booking_id: str, new_flight: str, new_departure: str) -> dict:
    booking = _BOOKINGS.get(booking_id.upper())
    if not booking:
        return {"error": "Booking not found during execution."}
    old_flight = booking["flight"]
    booking["flight"] = new_flight
    booking["departure"] = new_departure
    _PROCESSED[booking_id] = f"rebooked_to_{new_flight}"
    return {
        "success": True,
        "booking_id": booking_id,
        "old_flight": old_flight,
        "new_flight": new_flight,
        "message": f"Successfully rebooked from {old_flight} to {new_flight}.",
    }


@mcp.tool()
def apply_voucher(booking_id: str, flight_number: str, delay_minutes: int) -> dict:
    """Issue a travel voucher for a significant airline-caused delay.

    ⚠️  IRREVERSIBLE ACTION — triggers human sign-off.

    Policy: Vouchers issued for delays ≥3 hours caused by the airline.
    Amount: $50 per full hour of delay (max $300).

    Args:
        booking_id: The passenger's booking ID.
        flight_number: The affected flight number.
        delay_minutes: The total delay in minutes.

    Returns:
        Approval-pending payload, or an error dict.
    """
    if delay_minutes < 180:
        return {
            "error": f"Delay of {delay_minutes} minutes does not meet the 3-hour threshold for a voucher."
        }

    booking = _BOOKINGS.get(booking_id.upper())
    if not booking:
        return {"error": f"Booking '{booking_id}' not found."}

    hours = delay_minutes // 60
    voucher_amount = min(hours * 50.0, 300.0)

    return {
        "__requires_approval__": True,
        "action": "apply_voucher",
        "booking_id": booking_id,
        "flight_number": flight_number,
        "delay_minutes": delay_minutes,
        "voucher_amount": voucher_amount,
        "passenger": booking["passenger"],
        "message": (
            f"Issuing ${voucher_amount:.2f} travel voucher to {booking['passenger']} "
            f"for {delay_minutes}-minute delay on {flight_number}. This action is irreversible."
        ),
    }


def _execute_apply_voucher(booking_id: str, voucher_amount: float) -> dict:
    return {
        "success": True,
        "booking_id": booking_id,
        "voucher_amount": voucher_amount,
        "voucher_code": f"VCHR-{booking_id}-{int(voucher_amount)}",
        "message": f"Voucher of ${voucher_amount:.2f} issued. Code will be emailed to the passenger.",
    }


EXECUTION_HANDLERS: dict[str, callable] = {
    "cancel_flight": _execute_cancel_flight,
    "rebook_flight": _execute_rebook_flight,
    "apply_voucher": _execute_apply_voucher,
}


if __name__ == "__main__":
    mcp.run(transport="stdio")
