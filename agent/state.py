"""
AgentState definition for the multi-step ReAct agent.
Shared across all LangGraph nodes.
"""
from __future__ import annotations

import operator
from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """Complete state that flows through the LangGraph graph.

    Attributes:
        messages: Accumulated conversation history (auto-appended via operator.add).
        domain: Customer support domain — "retail" | "airline" | "telecom".
        pending_approval: Payload waiting for a human sign-off (None when clear).
        approved: Human decision on the pending action (None = not yet decided).
        guardrail_flags: List of policy/safety violations flagged so far.
        step_count: Number of completed ReAct reasoning steps.
        escalated: True if the agent escalated the conversation to a human agent.
        tool_call_log: Structured log of every tool invocation for eval metrics.
    """

    messages: Annotated[Sequence[BaseMessage], operator.add]
    domain: str
    pending_approval: dict | None
    approved: bool | None
    guardrail_flags: list[str]
    step_count: int
    escalated: bool
    tool_call_log: list[dict]
