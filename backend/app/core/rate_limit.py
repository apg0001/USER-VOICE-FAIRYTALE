import time
from dataclasses import dataclass
from threading import Lock


@dataclass(slots=True)
class Window:
    started: float
    count: int


class FixedWindowRateLimiter:
    """Bounded process-local guard; production ingress should enforce a shared limit too."""

    def __init__(self, requests_per_minute: int) -> None:
        self.limit = requests_per_minute
        self._windows: dict[str, Window] = {}
        self._lock = Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if window is None or now - window.started >= 60:
                self._windows[key] = Window(started=now, count=1)
                return True, 0
            if window.count >= self.limit:
                return False, max(1, round(60 - (now - window.started)))
            window.count += 1
            return True, 0
