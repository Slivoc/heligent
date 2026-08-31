from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Callable


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    reason: str | None
    retry_after_seconds: int
    client_limit: int
    client_remaining: int


class QueryRateLimiter:
    """Small in-process limiter for a single public demo server."""

    def __init__(
        self,
        *,
        per_client_limit: int = 10,
        per_client_window_seconds: int = 60,
        global_limit: int = 250,
        global_window_seconds: int = 86_400,
        max_concurrent: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        values = {
            "per_client_limit": per_client_limit,
            "per_client_window_seconds": per_client_window_seconds,
            "global_limit": global_limit,
            "global_window_seconds": global_window_seconds,
            "max_concurrent": max_concurrent,
        }
        if any(isinstance(value, bool) or value < 1 for value in values.values()):
            raise ValueError("Public query-limit settings must be positive integers")
        self.per_client_limit = per_client_limit
        self.per_client_window_seconds = per_client_window_seconds
        self.global_limit = global_limit
        self.global_window_seconds = global_window_seconds
        self.max_concurrent = max_concurrent
        self._clock = clock
        self._client_events: dict[str, deque[float]] = {}
        self._global_events: deque[float] = deque()
        self._in_flight = 0
        self._lock = Lock()

    def acquire(self, client_key: str) -> RateLimitDecision:
        now = self._clock()
        with self._lock:
            self._prune(now)
            events = self._client_events.setdefault(client_key, deque())
            if len(events) >= self.per_client_limit:
                return self._denied(
                    "client_rate_limit",
                    events[0] + self.per_client_window_seconds - now,
                    events,
                )
            if len(self._global_events) >= self.global_limit:
                return self._denied(
                    "global_rate_limit",
                    self._global_events[0] + self.global_window_seconds - now,
                    events,
                )
            if self._in_flight >= self.max_concurrent:
                return self._denied("busy", 3, events)

            events.append(now)
            self._global_events.append(now)
            self._in_flight += 1
            return RateLimitDecision(
                allowed=True,
                reason=None,
                retry_after_seconds=0,
                client_limit=self.per_client_limit,
                client_remaining=max(0, self.per_client_limit - len(events)),
            )

    def release(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def _prune(self, now: float) -> None:
        global_cutoff = now - self.global_window_seconds
        while self._global_events and self._global_events[0] <= global_cutoff:
            self._global_events.popleft()

        client_cutoff = now - self.per_client_window_seconds
        empty_clients: list[str] = []
        for client_key, events in self._client_events.items():
            while events and events[0] <= client_cutoff:
                events.popleft()
            if not events:
                empty_clients.append(client_key)
        for client_key in empty_clients:
            del self._client_events[client_key]

    def _denied(
        self,
        reason: str,
        retry_after: float,
        events: deque[float],
    ) -> RateLimitDecision:
        return RateLimitDecision(
            allowed=False,
            reason=reason,
            retry_after_seconds=max(1, math.ceil(retry_after)),
            client_limit=self.per_client_limit,
            client_remaining=max(0, self.per_client_limit - len(events)),
        )
