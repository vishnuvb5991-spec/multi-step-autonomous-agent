"""
Arize Phoenix OpenTelemetry tracing setup.
Call init_tracing() once at application startup, before importing LangGraph.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def init_tracing() -> None:
    """Register the OpenTelemetry tracer provider with Arize Phoenix.

    Reads from environment:
      - DISABLE_TRACING            (if true/1, tracing is skipped)
      - PHOENIX_PROJECT            (default: "multi-step-agent")
      - PHOENIX_COLLECTOR_ENDPOINT (default: "http://localhost:6006/v1/traces")

    All LangGraph node invocations, tool calls, LLM requests, and
    interrupt/resume events are automatically captured as nested spans.
    """
    if os.getenv("DISABLE_TRACING", "").lower() in ("true", "1", "yes"):
        logger.info("Tracing is disabled via DISABLE_TRACING.")
        return

    project_name = os.getenv("PHOENIX_PROJECT", "multi-step-agent")
    collector_endpoint = os.getenv(
        "PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces"
    )

    try:
        from phoenix.otel import register

        register(
            project_name=project_name,
            endpoint=collector_endpoint,
            auto_instrument=True,  # Auto-hooks LangChain / LangGraph
        )
        logger.info(
            "Arize Phoenix tracing initialized — project='%s', endpoint='%s'",
            project_name,
            collector_endpoint,
        )
    except ImportError:
        logger.warning(
            "arize-phoenix-otel not installed. Tracing disabled. "
            "Install with: pip install arize-phoenix-otel openinference-instrumentation-langchain"
        )
    except Exception as exc:
        logger.warning("Failed to initialize Arize Phoenix tracing: %s", exc)
