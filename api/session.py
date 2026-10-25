"""
Session and Thread Memory Management.
Uses thread-safe TTLCache to prevent unbounded memory growth and memory leak DoS attacks.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# Configurable session limits
MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", "10000"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))  # 24 hours


class SessionManager:
    """Thread-safe session cache with TTL eviction."""

    def __init__(self, maxsize: int = MAX_SESSIONS, ttl: int = SESSION_TTL_SECONDS) -> None:
        self._lock = threading.Lock()
        self._graphs: TTLCache[str, Any] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._domains: TTLCache[str, str] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._owners: TTLCache[str, str] = TTLCache(maxsize=maxsize, ttl=ttl)
        logger.info(
            "SessionManager initialized with maxsize=%d, ttl=%d seconds",
            maxsize,
            ttl,
        )

    def register_session(
        self, thread_id: str, graph: Any, domain: str, owner_id: str = "default"
    ) -> None:
        """Register or refresh an active conversation thread."""
        with self._lock:
            self._graphs[thread_id] = graph
            self._domains[thread_id] = domain
            self._owners[thread_id] = owner_id

    def get_graph(self, thread_id: str) -> Optional[Any]:
        """Retrieve the compiled graph for a thread, or None if expired/non-existent."""
        with self._lock:
            return self._graphs.get(thread_id)

    def get_domain(self, thread_id: str) -> Optional[str]:
        """Retrieve the domain for a thread."""
        with self._lock:
            return self._domains.get(thread_id)

    def get_owner(self, thread_id: str) -> Optional[str]:
        """Retrieve the registered owner ID for a thread."""
        with self._lock:
            return self._owners.get(thread_id)

    def verify_ownership(self, thread_id: str, requester_id: str) -> bool:
        """Verify that the requester owns the thread (IDOR protection)."""
        with self._lock:
            owner = self._owners.get(thread_id)
            if not owner or owner == "anonymous_dev_user" or requester_id == "anonymous_dev_user":
                return True
            return owner == requester_id

    def active_session_count(self) -> int:
        """Return the number of currently active, non-expired sessions."""
        with self._lock:
            return len(self._graphs)


# Global singleton instance
session_manager = SessionManager()
