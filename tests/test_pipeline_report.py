"""The leads report: qualified playlists worth a curator hunt, best fit first."""

from datetime import UTC, date, datetime, time, timedelta

from core.models import PlaylistProfileFit
from core.profiles import create_profile, set_profile_active
from pipeline.report import format_report, qualified_playlists
from tests.factories import make_curator, make_outreach, make_playlist
from tests.profile_helpers import add_contents

TODAY = date(2026, 9, 14)


def at(days_ago: int) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_ago), time(12, 0), tzinfo=UTC)


def active_profile(session, name="Synman"):
    profile = add_contents(session, create_profile(session, name))
    set_profile_active(session, profile.id, True)
    return profile


def qualified(session, profile, *, name, score, artists, curator=None, status="qualified", days_ago=3):
    playlist = make_playlist(
        session,
        name=name,
        status=status,
        rejection_reason="no-fit" if status == "rejected" else None,
        curator=curator or make_curator(session, display_name=f"Curator of {name}"),
        followers=1200,
        size_band="500-2k",
        last_add_at=at(days_ago),
        description="Glitchy things",
    )
    session.add(
        PlaylistProfileFit(
            playlist_id=playlist.spotify_id,
            profile_id=profile.id,
            fit_score=score,
            reference_artists_present=list(artists),
            genre_tags=[],
            qualified=status == "qualified",
        )
    )
    session.flush()
    return playlist


def test_best_fit_first_with_the_details_needed_to_act(session):
    profile = active_profile(session)
    weaker = qualified(session, profile, name="Maybe", score=1 / 3, artists=["Plaid"])
    best = qualified(session, profile, name="Perfect", score=1.0, artists=["Autechre", "Plaid", "Aphex Twin"])
    qualified(session, profile, name="Rejected one", score=1.0, artists=["Plaid"], status="rejected")

    items = qualified_playlists(session, today=TODAY)

    assert [item.name for item in items] == ["Perfect", "Maybe"]
    top = items[0]
    assert top.url == f"https://open.spotify.com/playlist/{best.spotify_id}"
    assert top.curator_name == "Curator of Perfect"
    assert top.profile_name == "Synman"
    assert top.reference_artists_present == ("Aphex Twin", "Autechre", "Plaid")
    assert (top.followers, top.size_band, top.last_add_at) == (1200, "500-2k", at(3))
    assert weaker.spotify_id in {item.spotify_id for item in items}


def test_can_be_filtered_to_one_profile(session):
    synman = active_profile(session)
    other = active_profile(session, "Other")
    qualified(session, synman, name="For Synman", score=1.0, artists=["Plaid"])
    qualified(session, other, name="For Other", score=1.0, artists=["Plaid"])

    items = qualified_playlists(session, today=TODAY, profile_id=other.id)

    assert [item.name for item in items] == ["For Other"]


def test_curators_who_are_not_eligible_are_left_out(session):
    profile = active_profile(session)
    excluded = make_curator(session, excluded_at=at(10), exclusion_reason="bad-fit")
    recently_pitched = make_curator(session)
    make_outreach(session, recently_pitched, profile, TODAY - timedelta(days=5))
    qualified(session, profile, name="Excluded curator", score=1.0, artists=["Plaid"], curator=excluded)
    qualified(
        session, profile, name="Pitched last week", score=1.0, artists=["Plaid"], curator=recently_pitched
    )
    qualified(session, profile, name="Fresh", score=1.0, artists=["Plaid"])

    assert [item.name for item in qualified_playlists(session, today=TODAY)] == ["Fresh"]


def test_the_limit_is_respected(session):
    profile = active_profile(session)
    for n in range(4):
        qualified(session, profile, name=f"Playlist {n}", score=1.0, artists=["Plaid"])

    assert len(qualified_playlists(session, today=TODAY, limit=2)) == 2


def test_formatted_report_reads_like_a_list_of_leads(session):
    profile = active_profile(session)
    playlist = qualified(
        session, profile, name="Perfect", score=1.0, artists=["Autechre", "Plaid", "Aphex Twin"]
    )

    text = format_report(qualified_playlists(session, today=TODAY), today=TODAY)

    assert "Perfect" in text
    assert f"https://open.spotify.com/playlist/{playlist.spotify_id}" in text
    assert "Curator of Perfect" in text
    assert "Aphex Twin, Autechre, Plaid" in text
    assert "last add 3 days ago" in text
    assert "500-2k" in text


def test_empty_report_says_so_plainly():
    assert "No qualified playlists yet" in format_report([], today=TODAY)
