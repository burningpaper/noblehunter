"""Putting recent rejections back in the queue, for when the rules that rejected them change.

Rejections for no fit, too small or not alive are re-checked after 90 days anyway. When the rules
change (as when genre and Claude matches started to count), waiting 90 days to judge recent
rejections again would waste them, so this puts them straight back in the queue. Pay-to-play and
Spotify's own playlists are permanent and never come back.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.exclusion import RECHECKABLE_REJECTIONS
from core.models import Playlist, PlaylistStatus


def requeue_recent_rejections(session: Session, *, reason: str, since: datetime) -> int:
    """Make rejections with this reason, checked since `since`, candidates again. Returns how many."""
    if reason not in RECHECKABLE_REJECTIONS:
        allowed = ", ".join(sorted(RECHECKABLE_REJECTIONS))
        raise ValueError(f"“{reason}” rejections can't be re-checked early; only these can: {allowed}.")

    playlists = session.scalars(
        select(Playlist).where(
            Playlist.status == PlaylistStatus.REJECTED,
            Playlist.rejection_reason == reason,
            Playlist.last_checked_at >= since,
        )
    ).all()
    for playlist in playlists:
        playlist.status = PlaylistStatus.CANDIDATE
        playlist.rejection_reason = None
    session.flush()
    return len(playlists)
