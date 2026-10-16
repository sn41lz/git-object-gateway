"""Instance-scoped OTel spans; bounded metrics; no paths, source text, URLs or credentials."""

import json
import math
import os
import threading
from collections import deque
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


def percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else 0


class JsonExporter(SpanExporter):
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.fchmod(fd, 0o600)
        self._file = os.fdopen(fd, "a", encoding="utf-8")
        self._lock = threading.Lock()

    def export(self, spans) -> SpanExportResult:
        with self._lock:
            for span in spans:
                data = {
                    "name": span.name,
                    "trace_id": f"{span.context.trace_id:032x}",
                    "span_id": f"{span.context.span_id:016x}",
                    "duration_ms": (span.end_time - span.start_time) / 1e6,
                    "attributes": dict(span.attributes or {}),
                }
                self._file.write(json.dumps(data) + "\n")
            self._file.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self):
        with self._lock:
            if not self._file.closed:
                self._file.close()


class Telemetry:
    def __init__(self, trace_path: Path | None = None):
        self.provider = TracerProvider()
        if trace_path is not None:
            self.provider.add_span_processor(SimpleSpanProcessor(JsonExporter(trace_path)))
        self.tracer = self.provider.get_tracer("git-object-gateway", "0.1.0")
        self._latencies: deque[float] = deque(maxlen=1000)
        self._count = self._errors = self._bytes = 0
        self._lock = threading.Lock()

    def observe(self, duration_ms: float, status: int, byte_count: int) -> None:
        with self._lock:
            self._latencies.append(duration_ms)
            self._count += 1
            self._errors += int(status >= 400)
            self._bytes += byte_count

    def stats(self) -> dict[str, Any]:
        with self._lock:
            values = list(self._latencies)
            return {
                "requests": self._count,
                "errors": self._errors,
                "response_body_bytes": self._bytes,
                "latency_window": len(values),
                "p50_ms": percentile(values, 0.5),
                "p95_ms": percentile(values, 0.95),
            }

    def close(self) -> None:
        self.provider.shutdown()
