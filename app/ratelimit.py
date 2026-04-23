"""Per-API-key rate limiting across multiple windows.

Every authenticated request consults three sliding windows at once:

* **minute** — ``rate_limit_per_min``, typically 60
* **day**    — ``rate_limit_per_day``, e.g. 500
* **week**   — ``rate_limit_per_week``, e.g. 2000

A request is allowed only when **all three** windows have headroom. If any
one is exceeded, we reject with 429 and report which window tripped plus
the soonest moment it would free up again.

Storage — one :class:`collections.deque` per (key, window) pair holds the
monotonic timestamps of recent hits. On each check we drop stamps older
than that window's width, then compare the remaining count to the limit.

Scope — this is an **in-process** limiter. It is correct for Render's
single-worker starter plan but does **not** survive process restarts and
does **not** coordinate across workers. Trade-offs documented in-line at
each relevant piece of state; when we outgrow one worker we swap the
backing store to Redis and keep the :class:`InMemoryRateLimiter` API intact.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

logger = logging.getLogger(__name__)


# Canonical window widths. Kept as floats because ``time.monotonic()`` is a
# float. Order matters only for :func:`windows_for` — we return them shortest
# first so the 429 response mentions the most immediate recovery time when
# ties happen.
SECONDS_PER_MINUTE: float = 60.0
SECONDS_PER_DAY: float = 86_400.0
SECONDS_PER_WEEK: float = 604_800.0


class Window(NamedTuple):
    """One rate-limit rule to enforce for a single key."""

    name: str     # "minute" | "day" | "week"
    seconds: float
    limit: int    # <= 0 disables this window for the key


def windows_for(
    *,
    per_min: int | None,
    per_day: int | None,
    per_week: int | None,
) -> list[Window]:
    """Build the list of general (all-routes) windows for a partner.

    ``None`` or a non-positive value in any slot disables enforcement for
    that window — we still accept the traffic, just don't count against
    a cap there. This keeps Airtable misconfig (empty cell → None) from
    taking a partner offline.
    """
    rules: list[Window] = []
    if per_min and per_min > 0:
        rules.append(Window("minute", SECONDS_PER_MINUTE, int(per_min)))
    if per_day and per_day > 0:
        rules.append(Window("day", SECONDS_PER_DAY, int(per_day)))
    if per_week and per_week > 0:
        rules.append(Window("week", SECONDS_PER_WEEK, int(per_week)))
    return rules


def windows_for_searches(
    *,
    per_day: int | None,
    per_week: int | None,
) -> list[Window]:
    """Build the search-creation-specific windows for a partner.

    Applied **only** to ``POST /v1/searches``; polling status and fetching
    matches continue to hit the general buckets above. Window names are
    prefixed ``searches_`` so the 429 response + ``X-RateLimit-Window``
    header clearly distinguish the two bucket families.
    """
    rules: list[Window] = []
    if per_day and per_day > 0:
        rules.append(Window("searches_day", SECONDS_PER_DAY, int(per_day)))
    if per_week and per_week > 0:
        rules.append(Window("searches_week", SECONDS_PER_WEEK, int(per_week)))
    return rules


@dataclass(frozen=True)
class WindowState:
    """Observed state of a single window after a check."""

    name: str
    limit: int
    remaining: int
    reset_in_seconds: float


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a :meth:`InMemoryRateLimiter.check` call.

    On ``allowed=False``, :attr:`tripped` is the window that said no;
    :attr:`windows` reports every window's remaining headroom either way,
    which we surface as ``X-RateLimit-*`` response headers on the happy path.
    """

    allowed: bool
    windows: list[WindowState]
    tripped: WindowState | None = None

    @property
    def tightest(self) -> WindowState | None:
        """The window with the smallest ``remaining``. Used for the happy-
        path ``X-RateLimit-*`` headers so we always report the bottleneck."""
        if not self.windows:
            return None
        return min(self.windows, key=lambda w: w.remaining)


class InMemoryRateLimiter:
    """Multi-window sliding-log limiter.

    Construct one per process — :func:`create_app` does exactly that and
    shares it via the :func:`get_limiter` dependency. Thread-safe via a
    single lock; at our target volumes lock contention is negligible.
    """

    def __init__(self) -> None:
        # Keyed by (partner_record_id, window_name) → deque of monotonic
        # timestamps. Separate deque per window so aging out stale stamps
        # is O(1) per hit regardless of window width.
        self._buckets: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, windows: list[Window]) -> RateLimitDecision:
        """Record one hit against ``key`` and decide whether to allow it.

        If any window is over its limit, no stamp is recorded and the
        request is rejected — this preserves the invariant that a 429 does
        **not** consume quota. Over-limit clients that keep hammering will
        therefore not keep their buckets permanently full.
        """
        if not windows:
            # No caps configured at all. Unlimited.
            return RateLimitDecision(allowed=True, windows=[])

        now = time.monotonic()

        with self._lock:
            # First pass — check every window. Don't mutate yet: we only
            # commit the hit if all windows have room.
            observed: list[WindowState] = []
            tripped: WindowState | None = None
            for win in windows:
                bucket = self._buckets.get((key, win.name))
                if bucket is None:
                    bucket = deque()
                    self._buckets[(key, win.name)] = bucket
                cutoff = now - win.seconds
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()

                count = len(bucket)
                if count >= win.limit:
                    oldest = bucket[0]
                    reset_in = max(0.0, (oldest + win.seconds) - now)
                    state = WindowState(
                        name=win.name,
                        limit=win.limit,
                        remaining=0,
                        reset_in_seconds=reset_in,
                    )
                    observed.append(state)
                    # First tripped window wins — we list them
                    # shortest→longest in :func:`windows_for`, so this is
                    # the most imminent recovery the client can expect.
                    if tripped is None:
                        tripped = state
                else:
                    observed.append(
                        WindowState(
                            name=win.name,
                            limit=win.limit,
                            remaining=win.limit - count - 1,  # -1 for this hit
                            reset_in_seconds=win.seconds,
                        )
                    )

            if tripped is not None:
                # Rebuild ``observed`` with the pre-hit remaining values for
                # non-tripped windows (we optimistically subtracted one
                # above assuming the hit would land).
                fixed: list[WindowState] = []
                for win, state in zip(windows, observed, strict=True):
                    if state is tripped or state.remaining == 0:
                        fixed.append(state)
                    else:
                        bucket = self._buckets[(key, win.name)]
                        fixed.append(
                            WindowState(
                                name=win.name,
                                limit=win.limit,
                                remaining=win.limit - len(bucket),
                                reset_in_seconds=win.seconds,
                            )
                        )
                return RateLimitDecision(
                    allowed=False, windows=fixed, tripped=tripped
                )

            # All clear — commit the hit.
            for win in windows:
                self._buckets[(key, win.name)].append(now)
            return RateLimitDecision(allowed=True, windows=observed)

    # Test helper: let tests reset state between cases without leaking
    # a private attribute access pattern across the codebase.
    def _reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# Module-level singleton. Shared across requests; resets only on process
# restart — see module docstring for scope limits.
rate_limiter = InMemoryRateLimiter()


def get_limiter() -> InMemoryRateLimiter:
    """FastAPI dependency that yields the shared limiter."""
    return rate_limiter
