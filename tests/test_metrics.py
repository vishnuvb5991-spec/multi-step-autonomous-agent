"""
Unit tests for the eval metrics module.
"""
from __future__ import annotations

import pytest
from eval.metrics import (
    aggregate_metrics,
    compute_pass_k,
    escalation_rate,
    task_success_rate,
    tool_accuracy,
)


class TestTaskSuccessRate:
    def test_all_success(self):
        assert task_success_rate([True, True, True]) == 1.0

    def test_all_fail(self):
        assert task_success_rate([False, False]) == 0.0

    def test_mixed(self):
        assert task_success_rate([True, False, True, True]) == pytest.approx(0.75)

    def test_empty(self):
        assert task_success_rate([]) == 0.0


class TestToolAccuracy:
    def test_all_correct(self):
        logs = [[{"tool": "look_up_order", "correct": True}]]
        assert tool_accuracy(logs) == 1.0

    def test_none_correct(self):
        logs = [[{"tool": "wrong_tool", "correct": False}]]
        assert tool_accuracy(logs) == 0.0

    def test_mixed(self):
        logs = [
            [{"tool": "look_up_order", "correct": True}, {"tool": "bad_tool", "correct": False}],
            [{"tool": "process_refund", "correct": True}],
        ]
        # 2 correct out of 3 total
        assert tool_accuracy(logs) == pytest.approx(2 / 3)

    def test_empty(self):
        assert tool_accuracy([]) == 0.0


class TestEscalationRate:
    def test_none_escalated(self):
        assert escalation_rate([False, False, False]) == 0.0

    def test_all_escalated(self):
        assert escalation_rate([True, True]) == 1.0

    def test_half_escalated(self):
        assert escalation_rate([True, False]) == pytest.approx(0.5)


class TestPassK:
    def test_all_succeed(self):
        assert compute_pass_k([True, True, True], k=3) == 1.0

    def test_all_fail(self):
        assert compute_pass_k([False, False, False], k=3) == 0.0

    def test_one_success_k3(self):
        # P(at least 1 success in 3) when 1/3 succeed empirically
        result = compute_pass_k([True, False, False], k=3)
        assert 0.0 < result < 1.0

    def test_perfect_k1(self):
        assert compute_pass_k([True], k=1) == 1.0

    def test_empty(self):
        assert compute_pass_k([], k=3) == 0.0


class TestAggregateMetrics:
    def test_basic_aggregate(self):
        results = [
            {"task_id": "t1", "domain": "retail", "outcomes": [True, True], "pass_k": 1.0, "escalated": False, "tool_log": [{"tool": "look_up_order", "correct": True}]},
            {"task_id": "t2", "domain": "retail", "outcomes": [False, False], "pass_k": 0.0, "escalated": True, "tool_log": [{"tool": "wrong", "correct": False}]},
        ]
        report = aggregate_metrics(results)
        assert report["task_success_rate"] == pytest.approx(0.5)
        assert report["escalation_rate"] == pytest.approx(0.5)
        assert "retail" in report["per_domain"]
        assert report["n_tasks"] == 2
