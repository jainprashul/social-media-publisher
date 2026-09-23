"""Deterministic retry classification and scheduling."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

RETRYABLE = "retryable"
PERMANENT = "permanent"
UNKNOWN = "unknown"

@dataclass(frozen=True)
class RetryDecision:
    classification: str
    error_class: str
    retryable: bool


def classify_error(error):
    name = getattr(error, "error_class", "") or type(error).__name__
    text = str(error).lower()
    name_l = name.lower()
    if any(x in name_l or x in text for x in ("timeout", "rate_limit", "ratelimit", "temporarily", "5xx", "server_error", "connection")):
        return RetryDecision(RETRYABLE, name, True)
    if any(x in name_l or x in text for x in ("auth", "authentication", "permission", "forbidden", "validation", "malformed", "invalid")):
        return RetryDecision(PERMANENT, name, False)
    if any(x in name_l or x in text for x in ("unknown", "ambiguous", "remote_state")):
        return RetryDecision(UNKNOWN, name, False)
    return RetryDecision(PERMANENT, name, False)


def backoff_seconds(attempt, base=60, maximum=3600):
    """Return bounded, jitter-free exponential delay; attempt 1 is base."""
    return min(maximum, base * (2 ** max(0, int(attempt) - 1)))


def next_attempt_at(attempt, now=None, base=60, maximum=3600):
    when = now or datetime.now(timezone.utc)
    if isinstance(when, str): when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    return (when + timedelta(seconds=backoff_seconds(attempt, base, maximum))).isoformat()


def retry_due(value, now=None):
    if not value: return True
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when <= (now or datetime.now(timezone.utc))
