"""
Unit tests for externalized system prompt loading.
"""
from __future__ import annotations

import os
from agent.prompts import get_system_prompt


def test_default_prompt_loading():
    retail_prompt = get_system_prompt("retail")
    assert "Retail / E-commerce" in retail_prompt
    assert "{max_steps}" not in retail_prompt


def test_env_override_prompt(monkeypatch):
    custom_prompt = "Custom Proprietary Policy: Never refund. Steps allowed: {max_steps}"
    monkeypatch.setenv("RETAIL_SYSTEM_PROMPT", custom_prompt)
    loaded = get_system_prompt("retail", max_steps=5)
    assert "Custom Proprietary Policy" in loaded
    assert "Steps allowed: 5" in loaded
