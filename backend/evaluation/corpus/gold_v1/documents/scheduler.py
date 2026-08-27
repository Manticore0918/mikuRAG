from datetime import datetime, timedelta


def compute_backoff(attempt: int) -> int:
    """Return exponential retry delay in seconds, capped at two minutes."""
    if attempt < 0:
        raise ValueError("attempt cannot be negative")
    return min((2**attempt) * 3, 120)


def should_retry(status_code: int) -> bool:
    """Retry timeouts, throttling, and server failures."""
    return status_code in {408, 429} or status_code >= 500


def parse_window(start: datetime, end: datetime) -> timedelta:
    """Validate a positive maintenance window no longer than six hours."""
    duration = end - start
    if duration <= timedelta(0) or duration > timedelta(hours=6):
        raise ValueError("window must be positive and at most six hours")
    return duration


class LeaseGuard:
    def __init__(self, owner: str | None, expires_at: datetime | None) -> None:
        self.owner = owner
        self.expires_at = expires_at

    def can_acquire(self, now: datetime) -> bool:
        return self.owner is None or self.expires_at is None or self.expires_at <= now

    def renew(self, actor: str, until: datetime) -> None:
        if actor != self.owner:
            raise PermissionError("only the lease owner can renew")
        self.expires_at = until


def normalize_job_id(value: str) -> str:
    """Normalize a display value for scheduler lookup."""
    return "-".join(value.strip().casefold().split())
