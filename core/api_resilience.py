"""Per-run accounting for provider outages and request failures.

The benchmark runner uses two clocks: an effective task budget for useful
execution, and a larger wall-clock recovery cap.  This module records only
time spent in failed, retryable provider requests (including retry backoff),
so the runner can keep those two budgets separate without sharing state
between concurrent cases.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class APIIncident:
    """One failed completion attempt observed by the LLM wrapper."""

    started_at: float
    ended_at: float
    error_type: str
    message: str
    retryable: bool
    infrastructure: bool = False
    retry_delay_seconds: float = 0.0
    final_failure: bool = False

    @property
    def recovery_end(self) -> float:
        return self.ended_at + self.retry_delay_seconds


@dataclass
class APIReliabilityTracker:
    """Collect retry/outage intervals for exactly one Runner invocation."""

    incidents: list[APIIncident] = field(default_factory=list)

    def record_failure(
        self,
        *,
        started_at: float,
        ended_at: float,
        error: BaseException,
        retryable: bool,
        infrastructure: bool = False,
        retry_delay_seconds: float = 0.0,
        final_failure: bool = False,
    ) -> None:
        self.incidents.append(APIIncident(
            started_at=started_at,
            ended_at=ended_at,
            error_type=type(error).__name__,
            message=str(error)[:500],
            retryable=retryable,
            infrastructure=infrastructure,
            retry_delay_seconds=max(0.0, retry_delay_seconds),
            final_failure=final_failure,
        ))

    def recovery_seconds(self, now: float | None = None) -> float:
        """Return wall time covered by failed retryable requests, without overlap."""
        intervals = sorted(
            (incident.started_at, incident.recovery_end)
            for incident in self.incidents
            if incident.retryable
        )
        if not intervals:
            return 0.0

        merged: list[list[float]] = []
        for start, end in intervals:
            if now is not None:
                end = min(end, now)
            if end <= start:
                continue
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        return sum(end - start for start, end in merged)

    def summary(self) -> dict[str, Any]:
        retryable = [incident for incident in self.incidents if incident.retryable]
        terminal = [incident for incident in self.incidents if incident.final_failure]
        terminal_infrastructure = [
            incident for incident in terminal if incident.infrastructure
        ]
        by_error_type: dict[str, int] = {}
        for incident in self.incidents:
            by_error_type[incident.error_type] = (
                by_error_type.get(incident.error_type, 0) + 1
            )
        return {
            "incident_count": len(self.incidents),
            "retryable_incident_count": len(retryable),
            "terminal_failure_count": len(terminal),
            "terminal_infrastructure_failure_count": len(terminal_infrastructure),
            "recovery_seconds": round(self.recovery_seconds(), 3),
            "by_error_type": by_error_type,
            "terminal_failures": [
                {
                    "error_type": incident.error_type,
                    "message": incident.message,
                    "retryable": incident.retryable,
                    "infrastructure": incident.infrastructure,
                }
                for incident in terminal
            ],
        }


def is_infrastructure_error(exc: BaseException) -> bool:
    """Conservatively classify provider/transport failures for result reporting.

    Invalid prompts, authentication errors, and context-window errors are not
    infrastructure failures: retrying them with a larger wall-clock allowance
    would hide a real configuration or agent bug.
    """
    name = type(exc).__name__
    if name == "APICircuitOpenError":
        return True
    if name in {
        "APIConnectionError", "APIError", "APIResponseValidationError",
        "BadGatewayError", "InternalServerError", "MidStreamFallbackError",
        "RateLimitError", "ServiceUnavailableError", "Timeout",
        # requests/urllib3 errors raised by Docker SDK setup and other local
        # transport clients. They are infrastructure failures, not model
        # answers, and must not advance an evolution chain.
        "ReadTimeout", "ConnectTimeout", "ChunkedEncodingError",
        "ProtocolError", "NewConnectionError",
    }:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and (status_code == 429 or status_code >= 500):
        return True

    # Some OpenAI-compatible relays collapse all upstream faults into a plain
    # ``Exception`` without preserving the HTTP status or LiteLLM exception
    # class.  Classify only transport/provider wording here; prompt, auth and
    # context-window validation errors deliberately remain non-infrastructure.
    message = str(exc).lower()
    provider_failure_markers = (
        "bad gateway", "service unavailable", "gateway timeout",
        "status 500", "status 502", "status 503", "status 504", "status 529",
        "http 500", "http 502", "http 503", "http 504", "http 529",
        "upstream timeout", "upstream connection", "connection reset",
        "connection aborted", "remote protocol error", "request failed",
        "request error", "provider error", "provider failure",
        "model service error", "temporarily unavailable", "overloaded",
        "invalid upstream response", "malformed upstream response",
        "incomplete chunked read", "mid-stream",
        "read timed out", "connection pool", "docker daemon",
    )
    return any(marker in message for marker in provider_failure_markers)
