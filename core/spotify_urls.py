"""Spotify link helpers, with no heavy imports so the web app can use them too."""

import re

TRACK_URL = re.compile(
    r"^https://open\.spotify\.com/(?:intl-[a-z-]+/)?"  # optional locale path, e.g. intl-de/
    r"track/([A-Za-z0-9]{22})(?:[?#].*)?$"  # the 22-character track id, then tracking junk
)


def canonical_track_url(url: str) -> str | None:
    """The bare `https://open.spotify.com/track/<id>` form, or None if this isn't a track link."""
    match = TRACK_URL.match(str(url).strip())
    return f"https://open.spotify.com/track/{match.group(1)}" if match else None
