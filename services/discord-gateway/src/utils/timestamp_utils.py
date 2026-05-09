"""Discord timestamp formatting utilities."""

from datetime import datetime


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
