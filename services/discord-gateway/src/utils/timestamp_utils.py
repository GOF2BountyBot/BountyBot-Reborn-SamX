"""Discord timestamp formatting utilities."""

from datetime import UTC, datetime


def iso_to_discord_ts(iso_str: str | None, style: str = "R") -> str:
    """Convert ISO 8601 string to Discord timestamp format.

    Styles:
        R = relative ("in 6 hours", "2 hours ago")
        D = date ("April 5, 2026")
        F = full ("April 5, 2026 12:00 PM")
        f = short datetime ("April 5, 2026 12:00 PM")
        T = time ("12:00:00 PM")
        t = short time ("12:00 PM")
        d = short date ("04/05/2026")

    Returns fallback string "N/A" if iso_str is None or unparseable.
    """
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return f"<t:{int(dt.timestamp())}:{style}>"
    except (ValueError, TypeError, OSError):
        return "N/A"


def event_status_label(e: dict) -> str:
    """Compute the human-readable status portion of an event autocomplete label."""
    state = e.get("state", "")
    if state == "draft":
        return "draft"
    if state in ("ended", "cancelled"):
        return state
    if state == "active" and e.get("ends_at"):
        return f"ends in {fmt_delta(e['ends_at'])}"
    if state == "scheduled" and e.get("scheduled_start_at"):
        return f"starts in {fmt_delta(e['scheduled_start_at'])}"
    return state


def fmt_delta(iso: str | None) -> str:
    """Return 'Xd Yh' time-until string for an ISO 8601 timestamp.

    Returns 'ended' when the timestamp is in the past, '?' when unparseable.

    >>> from datetime import UTC, datetime, timedelta
    >>> fmt_delta((datetime.now(UTC) + timedelta(days=3, hours=4)).isoformat())
    '3d 4h'
    """
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        total = int((dt - datetime.now(UTC)).total_seconds())
        if total <= 0:
            return "ended"
        days, rem = divmod(total, 86400)
        hours = rem // 3600
        return f"{days}d {hours}h" if days else f"{hours}h"
    except (ValueError, TypeError):
        return "?"
