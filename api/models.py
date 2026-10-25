"""
Pydantic request/response models for the FastAPI server.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Incoming chat turn from the user or τ²-bench harness."""

    message: str = Field(..., description="The customer's message.")
    domain: str = Field(
        default="retail",
        description='Customer support domain: "retail" | "airline" | "telecom".',
    )
    thread_id: Optional[str] = Field(
        default=None,
        description="Thread ID for continuing an existing conversation. Omit to start a new one.",
    )


class ChatResponse(BaseModel):
    """Response from a /chat call."""

    thread_id: str = Field(..., description="Thread ID — use this to continue the conversation.")
    reply: str = Field(..., description="The agent's response text.")
    requires_approval: bool = Field(
        default=False,
        description="True if an irreversible action is pending and needs human sign-off.",
    )
    approval_payload: Optional[dict[str, Any]] = Field(
        default=None,
        description="Details of the action awaiting approval (populated when requires_approval=True).",
    )
    escalated: bool = Field(
        default=False,
        description="True if the agent escalated the issue to a human specialist.",
    )
    guardrail_flags: list[str] = Field(
        default_factory=list,
        description="List of guardrail violations detected during this turn.",
    )
    step_count: int = Field(
        default=0,
        description="Number of ReAct reasoning steps taken in this conversation.",
    )


class ApprovalRequest(BaseModel):
    """Human sign-off decision for a pending irreversible action."""

    thread_id: str = Field(..., description="Thread ID of the paused conversation.")
    approved: bool = Field(..., description="True to approve the action, False to reject it.")


class ApprovalResponse(BaseModel):
    """Response after processing a human approval."""

    thread_id: str
    reply: str
    completed: bool = True
    guardrail_flags: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    version: str = "0.2.0"
    domains: list[str] = ["retail", "airline", "telecom"]
    active_sessions: int = 0
