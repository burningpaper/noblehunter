"""Migration 0002: Spotify's own playlists get an honest, permanent rejection reason."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from core.exclusion import playlist_ids_to_skip
from core.models import Playlist, RejectionReason
from tests.factories import make_playlist

TODAY = date(2026, 9, 14)


def test_spotify_owned_is_a_valid_rejection_reason(session):
    playlist = make_playlist(session, status="rejected", rejection_reason=RejectionReason.SPOTIFY_OWNED)

    assert playlist.rejection_reason == "spotify-owned"


def test_unknown_rejection_reasons_are_still_refused(session):
    session.add(Playlist(spotify_id="1" * 22, name="Bad", status="rejected", rejection_reason="because"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_spotify_owned_playlists_are_never_rechecked(session):
    long_ago = datetime.combine(TODAY - timedelta(days=1000), time(2, 0), tzinfo=UTC)
    playlist = make_playlist(
        session, status="rejected", rejection_reason=RejectionReason.SPOTIFY_OWNED, last_checked_at=long_ago
    )

    assert playlist_ids_to_skip(session, [playlist.spotify_id], TODAY) == {playlist.spotify_id}
