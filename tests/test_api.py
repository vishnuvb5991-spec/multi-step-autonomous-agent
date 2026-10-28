"""
Unit tests for FastAPI endpoints and production hardening features.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.session import SessionManager, session_manager
import api.auth as auth_module

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "retail" in data["domains"]
    assert "airline" in data["domains"]
    assert "telecom" in data["domains"]
    assert "active_sessions" in data


def test_invalid_domain_rejected():
    response = client.post(
        "/chat",
        json={"message": "hello", "domain": "banking"},
    )
    assert response.status_code == 422


def test_approve_unknown_thread():
    response = client.post(
        "/approve",
        json={"thread_id": "non-existent-thread-id-9999", "approved": True},
    )
    assert response.status_code == 404


def test_get_unknown_thread_state():
    response = client.get("/threads/non-existent-thread-id-9999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Production Hardening Tests
# ---------------------------------------------------------------------------

def test_cors_preflight_headers():
    """Verify CORS headers are present for allowed origins."""
    response = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-API-Key,Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "POST" in response.headers.get("access-control-allow-methods", "")


def test_session_manager_ttl_and_eviction():
    """Verify SessionManager tracks active sessions and respects maxsize via LRU eviction."""
    mgr = SessionManager(maxsize=2, ttl=60)
    assert mgr.active_session_count() == 0

    mgr.register_session("t1", "mock_graph_1", "retail", "user1")
    mgr.register_session("t2", "mock_graph_2", "airline", "user1")
    assert mgr.active_session_count() == 2

    # Registering third without accessing t1 should evict oldest (t1)
    mgr.register_session("t3", "mock_graph_3", "telecom", "user1")
    assert mgr.active_session_count() == 2
    assert mgr.get_graph("t1") is None
    assert mgr.get_graph("t2") == "mock_graph_2"
    assert mgr.get_graph("t3") == "mock_graph_3"


def test_api_authentication_enforcement(monkeypatch):
    """Verify 401 when AGENT_API_KEY is set and wrong/missing key is passed."""
    monkeypatch.setattr(auth_module, "EXPECTED_API_KEY", "secret-test-key-12345")

    # Missing auth
    resp = client.get("/threads/mock-thread-id")
    assert resp.status_code == 401
    assert "Authentication required" in resp.json()["detail"]

    # Invalid auth
    resp = client.get(
        "/threads/mock-thread-id",
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401
    assert "Invalid API key" in resp.json()["detail"]


def test_idor_thread_ownership_protection():
    """Verify that user A cannot access a thread owned by user B."""
    session_manager.register_session(
        thread_id="thread-user-a",
        graph={"mock": True},
        domain="retail",
        owner_id="user_a_key",
    )

    # Attempt access as user B
    assert session_manager.verify_ownership("thread-user-a", "user_b_key") is False
    # Attempt access as user A
    assert session_manager.verify_ownership("thread-user-a", "user_a_key") is True


def test_root_serves_chat_webpage():
    """GET / should return 200 with the chat webpage HTML."""
    resp = client.get("/")
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    assert "text/html" in ct, f"Expected HTML content-type, got: {ct}"
    body = resp.text
    assert "Multi-Step AI Agent" in body, "Page title not found in HTML"
    assert "domain-pills" in body, "Domain pills element not found in HTML"


def test_static_assets_served():
    """CSS and JS static assets should be accessible."""
    css_resp = client.get("/static/style.css")
    assert css_resp.status_code == 200
    assert "text/css" in css_resp.headers.get("content-type", "")

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "javascript" in js_resp.headers.get("content-type", "")
