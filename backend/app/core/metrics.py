from collections import defaultdict
from threading import Lock


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(values: tuple[str, ...], names: tuple[str, ...]) -> str:
    return ",".join(f'{name}="{_escape(value)}"' for name, value in zip(names, values, strict=True))


class MetricsRegistry:
    """Small process-local Prometheus exporter with bounded label sets."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self._duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._duration_count: dict[tuple[str, str], int] = defaultdict(int)
        self._cleanup: dict[str, int] = defaultdict(int)

    def observe_request(self, method: str, route: str, status: int, duration: float) -> None:
        route = route if route.startswith("/") else "unmatched"
        with self._lock:
            self._requests[(method, route, str(status))] += 1
            self._duration_sum[(method, route)] += duration
            self._duration_count[(method, route)] += 1

    def add_cleanup(self, values: dict[str, int]) -> None:
        with self._lock:
            for name, value in values.items():
                self._cleanup[name] += value

    def render(self) -> str:
        lines = [
            "# HELP voice_http_requests_total HTTP requests processed.",
            "# TYPE voice_http_requests_total counter",
        ]
        with self._lock:
            requests = sorted(self._requests.items())
            duration_sum = sorted(self._duration_sum.items())
            duration_count = sorted(self._duration_count.items())
            cleanup = sorted(self._cleanup.items())
        for request_labels, count in requests:
            lines.append(
                f"voice_http_requests_total"
                f"{{{_labels(request_labels, ('method', 'route', 'status'))}}} "
                f"{count}"
            )
        lines.extend(
            [
                "# HELP voice_http_request_duration_seconds_sum Total request duration.",
                "# TYPE voice_http_request_duration_seconds_sum counter",
            ]
        )
        for duration_labels, value in duration_sum:
            lines.append(
                f"voice_http_request_duration_seconds_sum"
                f"{{{_labels(duration_labels, ('method', 'route'))}}} {value:.6f}"
            )
        lines.extend(
            [
                "# HELP voice_http_request_duration_seconds_count Timed requests.",
                "# TYPE voice_http_request_duration_seconds_count counter",
            ]
        )
        for duration_labels, count in duration_count:
            lines.append(
                f"voice_http_request_duration_seconds_count"
                f"{{{_labels(duration_labels, ('method', 'route'))}}} {count}"
            )
        lines.extend(
            [
                "# HELP voice_cleanup_objects_total Cleanup outcomes by category.",
                "# TYPE voice_cleanup_objects_total counter",
            ]
        )
        for name, count in cleanup:
            lines.append(f'voice_cleanup_objects_total{{result="{_escape(name)}"}} {count}')
        return "\n".join(lines) + "\n"
