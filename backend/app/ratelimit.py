"""In-memory sign-in throttle (one process). Keyed by IP+account and by account."""

from __future__ import annotations

from collections import defaultdict, deque
import threading
import time


class LoginThrottle:
    def __init__(self, max_failures: int, window_seconds: int, clock=time.monotonic):
        self.max = max_failures
        self.window = window_seconds
        self.clock = clock
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _keys(self, ip: str, account: str) -> list[tuple[str, int]]:
        account = account.strip().lower()
        # Many IPs hammering one account lock it too, at 4x the per-IP limit.
        return [(f"ip:{ip}|{account}", self.max), (f"acct:{account}", self.max * 4)]

    def _prune(self, q: deque[float], now: float) -> None:
        while q and now - q[0] > self.window:
            q.popleft()

    def retry_after(self, ip: str, account: str) -> int:
        """Seconds until a new attempt is allowed (0 = allowed now)."""
        now = self.clock()
        wait = 0
        with self._lock:
            for key, limit in self._keys(ip, account):
                q = self._fails.get(key)  # .get: a read must not create entries (memory growth)
                if q is None:
                    continue
                self._prune(q, now)
                if not q:
                    del self._fails[key]
                elif len(q) >= limit:
                    wait = max(wait, int(self.window - (now - q[0])) + 1)
        return wait

    def failure(self, ip: str, account: str) -> None:
        now = self.clock()
        with self._lock:
            for key, _ in self._keys(ip, account):
                self._fails[key].append(now)

    def success(self, ip: str, account: str) -> None:
        with self._lock:
            self._fails.pop(self._keys(ip, account)[0][0], None)
