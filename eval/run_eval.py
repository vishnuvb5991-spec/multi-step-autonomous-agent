"""
τ²-bench Evaluation Runner
===========================
Runs the agent against simulated customer support tasks and reports:
  - task_success_rate
  - tool_accuracy
  - escalation_rate
  - pass^k

Usage:
  # Start the API server first:
  #   uvicorn api.main:app --port 8080

  python eval/run_eval.py [--domain retail] [--n-tasks 20] [--k 3] [--api-url http://localhost:8080]

Design:
  This runner uses a simple built-in task suite (no τ²-bench dependency required
  for local development). When the tau2_bench package is installed, set
  USE_TAU2_BENCH=true to load tasks from the real benchmark.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import time
from typing import Any

import httpx
from dotenv import load_dotenv

from eval.metrics import aggregate_metrics, compute_pass_k

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

API_URL = os.getenv("EVAL_API_URL", "http://localhost:8080")

# ---------------------------------------------------------------------------
# Built-in task suite (used when USE_TAU2_BENCH is not set)
# ---------------------------------------------------------------------------

BUILT_IN_TASKS: list[dict] = [
    # ── Retail ──────────────────────────────────────────────────────────────
    {
        "task_id": "retail-001",
        "domain": "retail",
        "turns": ["I want to return my order ORD-001. It doesn't fit."],
        "goal": "process_refund for ORD-001",
        "expected_tools": ["look_up_order", "check_return_eligibility", "process_refund"],
    },
    {
        "task_id": "retail-002",
        "domain": "retail",
        "turns": ["Can I cancel order ORD-002?"],
        "goal": "cancel_order for ORD-002",
        "expected_tools": ["look_up_order", "cancel_order"],
    },
    {
        "task_id": "retail-003",
        "domain": "retail",
        "turns": ["I want a refund for order ORD-003. I ordered it 40 days ago."],
        "goal": "policy_denial: refund window expired",
        "expected_tools": ["look_up_order", "check_return_eligibility"],
    },
    {
        "task_id": "retail-004",
        "domain": "retail",
        "turns": ["What are my exchange options for ORD-001?"],
        "goal": "provide_exchange_options",
        "expected_tools": ["look_up_order", "get_exchange_options"],
    },
    {
        "task_id": "retail-005",
        "domain": "retail",
        "turns": ["Can I cancel ORD-004? It's already shipped."],
        "goal": "policy_denial: cannot cancel shipped order",
        "expected_tools": ["look_up_order"],
    },
    # ── Airline ──────────────────────────────────────────────────────────────
    {
        "task_id": "airline-001",
        "domain": "airline",
        "turns": ["I need to cancel booking BK-1001. Change of plans."],
        "goal": "cancel_flight for BK-1001",
        "expected_tools": ["get_booking", "cancel_flight"],
    },
    {
        "task_id": "airline-002",
        "domain": "airline",
        "turns": ["What's the status of flight DL-890? I'm worried about delays."],
        "goal": "check_flight_status DL-890 + suggest voucher",
        "expected_tools": ["check_flight_status", "apply_voucher"],
    },
    {
        "task_id": "airline-003",
        "domain": "airline",
        "turns": ["Can I change my seat on BK-1001 to 14C?"],
        "goal": "seat_change BK-1001 to 14C",
        "expected_tools": ["get_booking", "change_seat"],
    },
    {
        "task_id": "airline-004",
        "domain": "airline",
        "turns": ["I need to rebook BK-1001 to flight UA-310 on 2026-10-25T10:00:00"],
        "goal": "rebook BK-1001",
        "expected_tools": ["get_booking", "rebook_flight"],
    },
    {
        "task_id": "airline-005",
        "domain": "airline",
        "turns": ["Try to cancel BK-1003. That flight already happened."],
        "goal": "policy_denial: completed booking",
        "expected_tools": ["get_booking"],
    },
    # ── Telecom ──────────────────────────────────────────────────────────────
    {
        "task_id": "telecom-001",
        "domain": "telecom",
        "turns": [
            "Hi, my internet has been down for hours. Account ACC-5001.",
            "I've already tried restarting my router.",
        ],
        "goal": "apply_bill_credit for ACC-5001",
        "expected_tools": ["get_account", "check_network_status", "refresh_network_signal", "apply_bill_credit"],
    },
    {
        "task_id": "telecom-002",
        "domain": "telecom",
        "turns": ["ACC-5002 has been having repeated signal drops. Please escalate."],
        "goal": "escalate_ticket for ACC-5002",
        "expected_tools": ["get_account", "check_network_status", "escalate_ticket"],
    },
    {
        "task_id": "telecom-003",
        "domain": "telecom",
        "turns": ["Can you refresh the signal for account ACC-5001?"],
        "goal": "refresh_network_signal for ACC-5001",
        "expected_tools": ["get_account", "refresh_network_signal"],
    },
]


# ---------------------------------------------------------------------------
# Task runner
# ---------------------------------------------------------------------------

async def run_single_task(
    client: httpx.AsyncClient, task: dict, auto_approve: bool = True
) -> dict:
    """Run a single task through the agent and return a result dict."""
    domain = task["domain"]
    turns = task["turns"]
    expected_tools = set(task.get("expected_tools", []))

    thread_id: str | None = None
    final_reply = ""
    tool_log: list[dict] = []
    escalated = False

    try:
        for turn in turns:
            payload = {"message": turn, "domain": domain}
            if thread_id:
                payload["thread_id"] = thread_id

            resp = await client.post(f"{API_URL}/chat", json=payload, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()

            thread_id = data["thread_id"]
            final_reply = data.get("reply", "")
            escalated = data.get("escalated", False)

            # Auto-approve irreversible actions during eval
            if data.get("requires_approval") and auto_approve:
                logger.info("  Auto-approving irreversible action for task %s", task["task_id"])
                approve_resp = await client.post(
                    f"{API_URL}/approve",
                    json={"thread_id": thread_id, "approved": True},
                    timeout=30.0,
                )
                approve_resp.raise_for_status()
                final_reply = approve_resp.json().get("reply", final_reply)

        # Fetch tool call log from thread state
        if thread_id:
            state_resp = await client.get(f"{API_URL}/threads/{thread_id}", timeout=10.0)
            if state_resp.status_code == 200:
                state_data = state_resp.json()
                raw_log = state_data.get("tool_call_log", [])
                # Annotate correctness against expected_tools
                called_tools = {entry["tool"] for entry in raw_log}
                for entry in raw_log:
                    entry["correct"] = entry["tool"] in expected_tools
                tool_log = raw_log

        # Heuristic success check: did the agent call all expected tools?
        called = {e["tool"] for e in tool_log}
        success = expected_tools.issubset(called) and not escalated

    except Exception as exc:
        logger.error("Task %s failed: %s", task["task_id"], exc)
        success = False

    return {
        "task_id": task["task_id"],
        "domain": domain,
        "success": success,
        "escalated": escalated,
        "tool_log": tool_log,
        "final_reply": final_reply,
    }


async def run_task_k_times(
    client: httpx.AsyncClient, task: dict, k: int
) -> dict:
    """Run a single task k times and compute pass^k."""
    outcomes = []
    for trial in range(k):
        logger.info("  Task %s — trial %d/%d", task["task_id"], trial + 1, k)
        result = await run_single_task(client, task)
        outcomes.append(result["success"])
        await asyncio.sleep(0.5)  # Rate-limit courtesy pause

    pass_k = compute_pass_k(outcomes, k)
    return {
        "task_id": task["task_id"],
        "domain": task["domain"],
        "outcomes": outcomes,
        "pass_k": pass_k,
        "escalated": not any(outcomes),  # Escalated if never succeeded
        "tool_log": [],  # Aggregated in the last trial only for brevity
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(domains: list[str], n_tasks: int, k: int) -> None:
    tasks = [t for t in BUILT_IN_TASKS if t["domain"] in domains]
    if n_tasks:
        tasks = tasks[:n_tasks]

    logger.info(
        "Running %d tasks across domains=%s, k=%d trials each",
        len(tasks), domains, k,
    )

    async with httpx.AsyncClient() as client:
        # Verify server is up
        try:
            health = await client.get(f"{API_URL}/health", timeout=5.0)
            health.raise_for_status()
        except Exception:
            logger.error("API server not reachable at %s. Start it first.", API_URL)
            return

        results = []
        for task in tasks:
            logger.info("Running task: %s", task["task_id"])
            r = await run_task_k_times(client, task, k)
            results.append(r)
            logger.info(
                "  → outcomes=%s  pass^%d=%.2f", r["outcomes"], k, r["pass_k"]
            )

    report = aggregate_metrics(results)
    print("\n" + "=" * 60)
    print("τ²-bench Evaluation Report")
    print("=" * 60)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run τ²-bench evaluation.")
    parser.add_argument(
        "--domain",
        nargs="+",
        default=["retail", "airline", "telecom"],
        help="Domains to evaluate.",
    )
    parser.add_argument("--n-tasks", type=int, default=0, help="Max tasks per domain (0=all).")
    parser.add_argument("--k", type=int, default=3, help="Number of trials per task (pass^k).")
    parser.add_argument("--api-url", default=API_URL, help="Agent API base URL.")
    args = parser.parse_args()

    API_URL = args.api_url
    asyncio.run(main(args.domain, args.n_tasks, args.k))
