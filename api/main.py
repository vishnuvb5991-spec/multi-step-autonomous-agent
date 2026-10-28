"""
FastAPI server for the Multi-Step Autonomous Customer Support Agent.

Endpoints:
  POST /chat           — Send a message; start or continue a conversation.
  POST /approve        — Human sign-off for a pending irreversible action.
  GET  /health         — Health check.
  GET  /threads/{id}   — Inspect the state of a conversation thread.

Usage:
  uvicorn api.main:app --reload --port 8080
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command

from api.auth import verify_api_key
from api.models import (
    ApprovalRequest,
    ApprovalResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
)
from api.session import session_manager
from tracing.setup import init_tracing

# Load .env (GEMINI_API_KEY, PHOENIX_* etc.)
load_dotenv()

# Must be called before LangGraph / LangChain are imported by other modules
init_tracing()

from agent.graph import get_or_build_graph  # noqa: E402 — must be after init_tracing

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

# ---------------------------------------------------------------------------
# Lifespan: pre-warm graphs at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("DISABLE_GRAPH_PREWARM", "").lower() in ("true", "1", "yes"):
        logger.info("Skipping graph pre-warming (DISABLE_GRAPH_PREWARM set).")
        yield
        return

    logger.info("Pre-warming domain graphs…")
    for domain in ("retail", "airline", "telecom"):
        try:
            await get_or_build_graph(domain)
            logger.info("Graph ready: %s", domain)
        except Exception as exc:
            logger.warning("Could not pre-warm '%s' graph: %s", domain, exc)
    yield
    logger.info("Shutting down.")


# ---------------------------------------------------------------------------
# App initialization & Production Middleware (CORS)
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Multi-Step Autonomous Customer Support Agent",
    description=(
        "ReAct-style agent evaluated on τ²-bench tasks across retail, airline, and telecom. "
        "Hardened for production with API authentication, TTL sessions, CORS, and multi-layer guardrails."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# Parse allowed origins from environment
raw_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:8080,http://127.0.0.1:8080,http://localhost:3000,http://127.0.0.1:3000",
)
allowed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health():
    """Health check — confirms the server is running and lists supported domains."""
    return HealthResponse(
        status="ok",
        active_sessions=session_manager.active_session_count(),
    )


@app.post("/chat", response_model=ChatResponse, tags=["agent"])
async def chat(req: ChatRequest, user: str = Depends(verify_api_key)):
    """Start or continue a multi-turn customer support conversation.

    Protected by API authentication.
    - If `thread_id` is omitted, a new conversation is created.
    - If the agent needs to perform an irreversible action, the response will
      contain `requires_approval=True` and an `approval_payload`. Call
      `POST /approve` with the `thread_id` to continue.
    """
    domain = req.domain.lower()
    if domain not in ("retail", "airline", "telecom"):
        raise HTTPException(status_code=422, detail=f"Unknown domain '{domain}'.")

    # Resolve thread
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # Verify ownership if continuing an existing thread (IDOR protection)
    if req.thread_id and not session_manager.verify_ownership(req.thread_id, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: thread belongs to another client.",
        )

    # Fetch or build the domain graph
    try:
        graph = await get_or_build_graph(domain)
    except Exception as exc:
        logger.error("Failed to build graph for domain '%s': %s", domain, exc)
        raise HTTPException(status_code=500, detail=f"Agent initialisation failed: {exc}")

    # Register in TTL-evicted session manager
    session_manager.register_session(thread_id, graph, domain, owner_id=user)

    # Build initial state for new threads; subsequent turns just pass the message
    initial_state = {
        "messages": [("user", req.message)],
        "domain": domain,
        "pending_approval": None,
        "approved": None,
        "guardrail_flags": [],
        "step_count": 0,
        "escalated": False,
        "tool_call_log": [],
    }

    try:
        result = await graph.ainvoke(initial_state, config)
    except Exception as exc:
        logger.error("Graph invocation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}")

    # Extract the agent's last text message
    last_msg = result["messages"][-1] if result["messages"] else None
    reply_text = getattr(last_msg, "content", "") or ""

    # Check if graph paused for human approval
    state_snapshot = graph.get_state(config)
    pending = None
    requires_approval = False

    if state_snapshot.tasks:
        for task in state_snapshot.tasks:
            interrupts = getattr(task, "interrupts", [])
            if interrupts:
                requires_approval = True
                pending = interrupts[0].value if interrupts else None
                if isinstance(pending, dict) and "prompt" in pending:
                    reply_text = pending["prompt"]
                break

    return ChatResponse(
        thread_id=thread_id,
        reply=reply_text,
        requires_approval=requires_approval,
        approval_payload=pending,
        escalated=result.get("escalated", False),
        guardrail_flags=result.get("guardrail_flags", []),
        step_count=result.get("step_count", 0),
    )


@app.post("/approve", response_model=ApprovalResponse, tags=["agent"])
async def approve(req: ApprovalRequest, user: str = Depends(verify_api_key)):
    """Submit a human approval or rejection for a pending irreversible action.

    Protected by API authentication & IDOR ownership verification.
    """
    graph = session_manager.get_graph(req.thread_id)
    if graph is None:
        raise HTTPException(
            status_code=404,
            detail=f"Thread '{req.thread_id}' not found. Has it expired or never started?",
        )

    if not session_manager.verify_ownership(req.thread_id, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: thread belongs to another client.",
        )

    config = {"configurable": {"thread_id": req.thread_id}}
    decision = "approve" if req.approved else "reject"

    try:
        result = await graph.ainvoke(Command(resume=decision), config)
    except Exception as exc:
        logger.error("Resume failed for thread '%s': %s", req.thread_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Resume error: {exc}")

    last_msg = result["messages"][-1] if result["messages"] else None
    reply_text = getattr(last_msg, "content", "") or ""

    return ApprovalResponse(
        thread_id=req.thread_id,
        reply=reply_text,
        completed=True,
        guardrail_flags=result.get("guardrail_flags", []),
    )


@app.get("/threads/{thread_id}", tags=["debug"], include_in_schema=True)
async def get_thread_state(thread_id: str, user: str = Depends(verify_api_key)):
    """Inspect the current state of a conversation thread.

    Protected by API authentication & IDOR ownership verification.
    """
    graph = session_manager.get_graph(thread_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found.")

    if not session_manager.verify_ownership(thread_id, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: thread belongs to another client.",
        )

    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)

    messages = []
    for msg in (snapshot.values or {}).get("messages", []):
        messages.append(
            {
                "type": type(msg).__name__,
                "content": getattr(msg, "content", ""),
                "tool_calls": getattr(msg, "tool_calls", []),
            }
        )

    return {
        "thread_id": thread_id,
        "domain": session_manager.get_domain(thread_id),
        "next": snapshot.next,
        "step_count": (snapshot.values or {}).get("step_count", 0),
        "escalated": (snapshot.values or {}).get("escalated", False),
        "guardrail_flags": (snapshot.values or {}).get("guardrail_flags", []),
        "tool_call_log": (snapshot.values or {}).get("tool_call_log", []),
        "messages": messages,
        "pending_tasks": len(snapshot.tasks),
    }


# ---------------------------------------------------------------------------
# Static file mount (Chat Webpage) — MUST be after all API routes
# ---------------------------------------------------------------------------
from pathlib import Path  # noqa: E402

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static-assets")
    # Serve index.html at "/" (html=True enables that)
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
