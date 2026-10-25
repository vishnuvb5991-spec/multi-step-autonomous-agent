"""
Evaluation metrics for τ²-bench task runs.

Metrics:
  - task_success_rate  : fraction of tasks where the final DB state matched the goal.
  - tool_accuracy      : fraction of tool calls that were the correct tool for the step.
  - escalation_rate    : fraction of tasks where the agent escalated instead of resolving.
  - pass_k             : P(task succeeds at least once in k independent trials).
"""
from __future__ import annotations

import math
import statistics
from typing import Sequence


def task_success_rate(outcomes: Sequence[bool]) -> float:
    """Fraction of tasks that succeeded (final state == goal state).

    Args:
        outcomes: Boolean success values, one per task.

    Returns:
        Success rate in [0.0, 1.0].
    """
    if not outcomes:
        return 0.0
    return sum(outcomes) / len(outcomes)


def tool_accuracy(tool_logs: Sequence[Sequence[dict]]) -> float:
    """Fraction of tool invocations that used the correct tool.

    τ²-bench tasks annotate which tool(s) should be called at each step.
    This metric compares the agent's actual calls against the reference.

    Args:
        tool_logs: List of per-task tool call logs.
                   Each entry is a list of dicts with keys: "tool", "correct" (bool).

    Returns:
        Accuracy in [0.0, 1.0], or 0.0 if no calls were logged.
    """
    total = 0
    correct = 0
    for task_log in tool_logs:
        for call in task_log:
            total += 1
            if call.get("correct", False):
                correct += 1
    return correct / total if total > 0 else 0.0


def escalation_rate(escalated_flags: Sequence[bool]) -> float:
    """Fraction of tasks where the agent escalated instead of resolving autonomously.

    Args:
        escalated_flags: Boolean values, one per task.

    Returns:
        Escalation rate in [0.0, 1.0].
    """
    if not escalated_flags:
        return 0.0
    return sum(escalated_flags) / len(escalated_flags)


def compute_pass_k(outcomes: Sequence[bool], k: int) -> float:
    """Compute pass^k: P(task succeeds at least once in k independent trials).

    Uses the unbiased estimator:
        pass^k = 1 - C(n-c, k) / C(n, k)

    where n = total trials, c = number of successes.

    Falls back to the simpler empirical formula when n < k.

    Args:
        outcomes: Boolean results of the k (or more) independent trials.
        k: Number of trials to evaluate.

    Returns:
        pass^k score in [0.0, 1.0].
    """
    n = len(outcomes)
    c = sum(outcomes)

    if n == 0:
        return 0.0
    if c == n:
        return 1.0
    if c == 0:
        return 0.0

    if n > k:
        # Unbiased estimator when pool of trials n > k
        try:
            numerator = math.comb(n - c, k)
            denominator = math.comb(n, k)
            return 1.0 - numerator / denominator
        except ValueError:
            pass

    # Binomial probability for k independent trials with success rate p = c / n
    p = c / n
    return 1.0 - (1.0 - p) ** k


def aggregate_metrics(results: list[dict]) -> dict:
    """Aggregate per-task result dicts into a final metric report.

    Each result dict should have:
      - "domain": str
      - "task_id": int
      - "outcomes": list[bool]   (k trial results)
      - "escalated": bool
      - "tool_log": list[dict]   (with "correct" bool per call)

    Returns:
        Dict with task_success_rate, tool_accuracy, escalation_rate, pass_k,
        and per-domain breakdowns.
    """
    all_outcomes = [r["outcomes"][-1] for r in results]  # last trial as primary
    all_escalated = [r.get("escalated", False) for r in results]
    all_tool_logs = [r.get("tool_log", []) for r in results]
    all_pass_k = [r.get("pass_k", 0.0) for r in results]

    # Per-domain breakdown
    domain_stats: dict[str, dict] = {}
    for r in results:
        d = r.get("domain", "unknown")
        if d not in domain_stats:
            domain_stats[d] = {"outcomes": [], "escalated": [], "pass_k": []}
        domain_stats[d]["outcomes"].append(r["outcomes"][-1])
        domain_stats[d]["escalated"].append(r.get("escalated", False))
        domain_stats[d]["pass_k"].append(r.get("pass_k", 0.0))

    per_domain = {}
    for d, stats in domain_stats.items():
        per_domain[d] = {
            "task_success_rate": task_success_rate(stats["outcomes"]),
            "escalation_rate": escalation_rate(stats["escalated"]),
            "pass_k": statistics.mean(stats["pass_k"]) if stats["pass_k"] else 0.0,
            "n_tasks": len(stats["outcomes"]),
        }

    return {
        "task_success_rate": task_success_rate(all_outcomes),
        "tool_accuracy": tool_accuracy(all_tool_logs),
        "escalation_rate": escalation_rate(all_escalated),
        "pass_k": statistics.mean(all_pass_k) if all_pass_k else 0.0,
        "n_tasks": len(results),
        "per_domain": per_domain,
    }
