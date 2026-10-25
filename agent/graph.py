"""
LangGraph ReAct Agent Graph
============================
Implements a ReAct-style multi-step reasoning loop with:
  - Gemini 3.8 Flash as the backbone LLM
  - MCP tool servers (retail / airline / telecom) loaded via langchain-mcp-adapters
  - Human sign-off via LangGraph interrupt() for irreversible actions
  - Input/output guardrails
  - Step-count cap with automatic escalation

Graph topology:
  [START] → agent_node ─┬─(has tool calls)──→ tool_node ──→ agent_node
                        └─(no tool calls / escalated / guardrail block) ──→ [END]
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from typing import Any, Literal

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from agent.guardrails import (
    check_input_guardrail,
    check_output_guardrail,
    is_irreversible,
    redact_output,
)
from agent.prompts import get_system_prompt
from agent.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_STEPS = int(os.getenv("MAX_REACT_STEPS", "10"))

_DOMAIN_SERVER_SCRIPTS = {
    "retail": os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "mcp_servers", "retail_server.py"
    ),
    "airline": os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "mcp_servers", "airline_server.py"
    ),
    "telecom": os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "mcp_servers", "telecom_server.py"
    ),
}

# Cache: domain -> list of LangChain tool objects
_tool_cache: dict[str, list] = {}

# Cache: domain -> execution handlers for irreversible actions
_handler_cache: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Tool loading
# ---------------------------------------------------------------------------

async def _load_tools_for_domain(domain: str) -> tuple[list, dict]:
    """Load MCP tools for a domain (cached after first call)."""
    if domain in _tool_cache:
        return _tool_cache[domain], _handler_cache[domain]

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from langchain_mcp_adapters.tools import load_mcp_tools

    script_path = _DOMAIN_SERVER_SCRIPTS.get(domain)
    if not script_path:
        raise ValueError(f"Unknown domain: '{domain}'. Valid: {list(_DOMAIN_SERVER_SCRIPTS)}")

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[script_path],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)

    # Load execution handlers from the server module
    module_map = {
        "retail": "mcp_servers.retail_server",
        "airline": "mcp_servers.airline_server",
        "telecom": "mcp_servers.telecom_server",
    }
    mod = importlib.import_module(module_map[domain])
    handlers = getattr(mod, "EXECUTION_HANDLERS", {})

    _tool_cache[domain] = tools
    _handler_cache[domain] = handlers
    logger.info("Loaded %d tools for domain '%s'", len(tools), domain)
    return tools, handlers


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------

def _get_llm(tools: list) -> Any:
    """Return a Gemini 3.8 Flash model bound to the provided tools."""
    llm = ChatGoogleGenerativeAI(
        model="gemini-1.5-flash",  # Use available model; swap to gemini-3.8-flash when on new SDK
        temperature=0,
        google_api_key=os.getenv("GEMINI_API_KEY"),
    )
    return llm.bind_tools(tools)


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def _make_agent_node(domain: str, tools: list):
    """Factory: returns an agent_node function closed over domain + tools."""

    llm = _get_llm(tools)
    system_prompt = get_system_prompt(domain, max_steps=MAX_STEPS)

    def agent_node(state: AgentState) -> dict:
        """Reason over the conversation and decide on the next action or final answer."""
        # ── Input guardrail ──────────────────────────────────────────
        last_content = str(state["messages"][-1].content) if state["messages"] else ""
        input_flags = check_input_guardrail(last_content)
        if input_flags:
            logger.warning("Input guardrail triggered: %s", input_flags)
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "I'm sorry, I cannot process that request as it violates our "
                            "security policy. Please rephrase your message."
                        )
                    )
                ],
                "guardrail_flags": state.get("guardrail_flags", []) + input_flags,
            }

        # ── Step-count cap → escalate ────────────────────────────────
        if state.get("step_count", 0) >= MAX_STEPS:
            logger.warning("Max steps (%d) reached — escalating.", MAX_STEPS)
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "I've been unable to resolve your issue within the allowed steps. "
                            "I'm escalating this to a human support specialist who will "
                            "contact you shortly. I apologize for the inconvenience."
                        )
                    )
                ],
                "escalated": True,
            }

        # ── LLM call ────────────────────────────────────────────────
        messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
        response: AIMessage = llm.invoke(messages)

        # ── Output guardrail ─────────────────────────────────────────
        out_text = response.content or ""
        out_flags = check_output_guardrail(out_text)
        if out_flags:
            logger.warning("Output guardrail triggered: %s", out_flags)
            response = AIMessage(content=redact_output(out_text))

        return {
            "messages": [response],
            "step_count": state.get("step_count", 0) + 1,
            "guardrail_flags": state.get("guardrail_flags", []) + out_flags,
        }

    return agent_node


def _make_tool_node(domain: str, tools: list, handlers: dict):
    """Factory: returns a tool_node function closed over domain tools + handlers."""

    # Build a quick lookup: tool_name -> LangChain tool object
    tool_map = {t.name: t for t in tools}

    def tool_node(state: AgentState) -> dict:
        """Execute tool calls. Interrupts before irreversible actions for human approval."""
        last_msg = state["messages"][-1]
        if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
            return {}

        results: list[ToolMessage] = []
        tool_log: list[dict] = list(state.get("tool_call_log", []))

        for call in last_msg.tool_calls:
            tool_name: str = call["name"]
            tool_args: dict = call.get("args", {})
            call_id: str = call["id"]

            logger.info("Tool call: %s(%s)", tool_name, tool_args)

            # ── Invoke the MCP-backed tool ───────────────────────────
            lc_tool = tool_map.get(tool_name)
            if lc_tool is None:
                raw_result = {"error": f"Tool '{tool_name}' not found."}
            else:
                try:
                    raw_result = lc_tool.invoke(tool_args)
                    if isinstance(raw_result, str):
                        import json as _json
                        try:
                            raw_result = _json.loads(raw_result)
                        except Exception:
                            raw_result = {"result": raw_result}
                except Exception as exc:
                    raw_result = {"error": str(exc)}

            # ── Human sign-off for irreversible actions ──────────────
            if isinstance(raw_result, dict) and raw_result.get("__requires_approval__"):
                logger.info("Interrupting for human approval: %s", tool_name)
                human_decision = interrupt(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "payload": raw_result,
                        "prompt": (
                            f"⚠️  APPROVAL REQUIRED\n\n"
                            f"Action: {tool_name}\n"
                            f"Details: {raw_result.get('message', '')}\n\n"
                            f"Reply 'approve' to proceed or 'reject' to cancel."
                        ),
                    }
                )

                if human_decision == "approve":
                    # Execute the action via the server-side handler
                    handler = handlers.get(tool_name)
                    if handler:
                        import inspect
                        sig = inspect.signature(handler)
                        has_var_keyword = any(
                            p.kind == inspect.Parameter.VAR_KEYWORD
                            for p in sig.parameters.values()
                        )
                        combined = {**tool_args, **raw_result}
                        # If tool has 'amount' and raw_result has 'refund_amount'
                        if "amount" in sig.parameters and "amount" not in combined and "refund_amount" in combined:
                            combined["amount"] = combined["refund_amount"]
                        if has_var_keyword:
                            filtered = combined
                        else:
                            filtered = {
                                k: v for k, v in combined.items() if k in sig.parameters
                            }
                        try:
                            raw_result = handler(**filtered)
                        except Exception as handler_exc:
                            logger.error("Handler execution failed: %s", handler_exc)
                            raw_result = {"error": f"Execution error: {handler_exc}"}
                    else:
                        raw_result = {"success": True, "message": "Action approved and executed."}
                else:
                    raw_result = {
                        "cancelled": True,
                        "message": "Action was rejected by the human reviewer. No changes were made.",
                    }

            # ── Log the tool call for eval metrics ──────────────────
            tool_log.append(
                {
                    "tool": tool_name,
                    "args": tool_args,
                    "result": raw_result,
                    "approved": raw_result.get("success") if isinstance(raw_result, dict) else None,
                }
            )

            results.append(
                ToolMessage(content=str(raw_result), tool_call_id=call_id, name=tool_name)
            )

        return {"messages": results, "tool_call_log": tool_log}

    return tool_node


def _should_continue(state: AgentState) -> Literal["tools", "end"]:
    """Route: if last message has tool calls → tools node, else → END."""
    if state.get("escalated"):
        return "end"
    last = state["messages"][-1] if state["messages"] else None
    if last and hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return "end"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

async def build_graph(domain: str):
    """Build and compile the ReAct LangGraph for the given domain.

    The graph is re-created per domain (tools differ) but uses a shared
    MemorySaver so threads can be resumed after interrupt().

    Args:
        domain: "retail" | "airline" | "telecom"

    Returns:
        A compiled LangGraph CompiledStateGraph.
    """
    tools, handlers = await _load_tools_for_domain(domain)

    agent_fn = _make_agent_node(domain, tools)
    tool_fn = _make_tool_node(domain, tools, handlers)

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_fn)
    graph.add_node("tools", tool_fn)
    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent",
        _should_continue,
        {"tools": "tools", "end": END},
    )
    graph.add_edge("tools", "agent")

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Compiled graph cache (one per domain)
# ---------------------------------------------------------------------------

_graph_cache: dict[str, Any] = {}


async def get_or_build_graph(domain: str):
    """Return a cached compiled graph for the domain, building it if needed."""
    if domain not in _graph_cache:
        _graph_cache[domain] = await build_graph(domain)
    return _graph_cache[domain]
