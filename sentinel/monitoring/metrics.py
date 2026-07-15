"""In-process metrics for The Sentinel (stdlib only).

One module-level registry (mirroring ``monitoring.logger``): counters, gauges,
and summaries behind a single lock — the background worker thread and the event
loop both write. Rendered in Prometheus text exposition format by the /metrics
endpoint, so a real Prometheus can scrape it without any client library.

Instrumentation is fire-and-forget: every public method swallows its own
errors, so a metrics bug can never fail a review (same ethos as the
failure-safe GitHub/LLM clients).
"""

import threading

_Key = tuple[str, tuple[tuple[str, str], ...]]


def _key(name: str, labels: dict | None) -> _Key:
    if not labels:
        return (name, ())
    return (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))


def _series(key: _Key) -> str:
    name, labels = key
    if not labels:
        return name
    inner = ",".join(f'{label}="{value}"' for label, value in labels)
    return f"{name}{{{inner}}}"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[_Key, float] = {}
        self._gauges: dict[_Key, float] = {}
        self._summaries: dict[_Key, dict[str, float]] = {}

    def counter_inc(self, name: str, labels: dict | None = None, value: float = 1.0) -> None:
        try:
            key = _key(name, labels)
            with self._lock:
                self._counters[key] = self._counters.get(key, 0.0) + float(value)
        except Exception:
            pass

    def gauge_set(self, name: str, value: float, labels: dict | None = None) -> None:
        try:
            key = _key(name, labels)
            with self._lock:
                self._gauges[key] = float(value)
        except Exception:
            pass

    def observe(self, name: str, value: float, labels: dict | None = None) -> None:
        """Record one observation into a summary (count/sum/min/max)."""
        try:
            key = _key(name, labels)
            observed = float(value)
            with self._lock:
                summary = self._summaries.get(key)
                if summary is None:
                    self._summaries[key] = {
                        "count": 1.0,
                        "sum": observed,
                        "min": observed,
                        "max": observed,
                    }
                else:
                    summary["count"] += 1.0
                    summary["sum"] += observed
                    summary["min"] = min(summary["min"], observed)
                    summary["max"] = max(summary["max"], observed)
        except Exception:
            pass

    def snapshot(self) -> dict:
        """Flat dict view (series-string keys) — for tests and debugging."""
        try:
            with self._lock:
                return {
                    "counters": {_series(k): v for k, v in self._counters.items()},
                    "gauges": {_series(k): v for k, v in self._gauges.items()},
                    "summaries": {_series(k): dict(v) for k, v in self._summaries.items()},
                }
        except Exception:
            return {"counters": {}, "gauges": {}, "summaries": {}}

    def render_prometheus(self) -> str:
        """Prometheus text exposition (version 0.0.4).

        Summaries emit ``_count``/``_sum`` (standard) plus ``_min``/``_max``
        (non-standard but harmless — scraped as untyped series).
        """
        try:
            with self._lock:
                counters = dict(self._counters)
                gauges = dict(self._gauges)
                summaries = {k: dict(v) for k, v in self._summaries.items()}

            lines: list[str] = []
            for family, kind in ((counters, "counter"), (gauges, "gauge")):
                for name in sorted({key[0] for key in family}):
                    lines.append(f"# TYPE {name} {kind}")
                    for key in sorted(k for k in family if k[0] == name):
                        lines.append(f"{_series(key)} {_number(family[key])}")
            for name in sorted({key[0] for key in summaries}):
                lines.append(f"# TYPE {name} summary")
                for key in sorted(k for k in summaries if k[0] == name):
                    stats = summaries[key]
                    base, labels = key
                    for stat in ("count", "sum", "min", "max"):
                        lines.append(f"{_series((f'{base}_{stat}', labels))} {_number(stats[stat])}")
            return "\n".join(lines) + ("\n" if lines else "")
        except Exception:
            return ""

    def reset(self) -> None:
        """Clear everything — tests only."""
        try:
            with self._lock:
                self._counters.clear()
                self._gauges.clear()
                self._summaries.clear()
        except Exception:
            pass


# Module singleton, like monitoring.logger's global configuration.
metrics = MetricsRegistry()
