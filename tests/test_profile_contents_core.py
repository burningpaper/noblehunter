"""Stage 2d rules: what goes inside a profile, and what happens when it stops being ready."""

import pytest

from core.models import PlaylistSource, SearchTermStatus
from core.profile_contents import (
    add_anti_signal,
    add_genre,
    add_reference_artist,
    add_search_term,
    add_track,
    move_genre,
    remove_anti_signal,
    remove_genre,
    remove_reference_artist,
    remove_search_term,
    remove_track,
    search_requests_per_night,
    set_search_term_status,
)
from core.profiles import ProfileValidationError, create_profile, set_profile_active
from tests.factories import make_playlist
from tests.profile_helpers import add_contents

TRACK = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"


@pytest.fixture
def profile(session):
    return create_profile(session, "Synman")


def ordered_tags(profile):
    return [genre.tag for genre in sorted(profile.genres, key=lambda g: g.priority)]


class TestGenres:
    def test_genres_are_added_in_priority_order(self, session, profile):
        add_genre(session, profile.id, "  IDM ")
        add_genre(session, profile.id, "braindance")

        assert ordered_tags(profile) == ["IDM", "braindance"]

    @pytest.mark.parametrize("duplicate", ["idm", "  IDM  ", "Idm"])
    def test_duplicate_genres_are_rejected(self, session, profile, duplicate):
        add_genre(session, profile.id, "IDM")

        with pytest.raises(ProfileValidationError) as error:
            add_genre(session, profile.id, duplicate)

        assert "already" in error.value.errors["tag"]

    @pytest.mark.parametrize("tag", ["", "   ", "x" * 101])
    def test_blank_or_long_genres_are_rejected(self, session, profile, tag):
        with pytest.raises(ProfileValidationError) as error:
            add_genre(session, profile.id, tag)

        assert "tag" in error.value.errors

    def test_moving_a_genre_up_and_down(self, session, profile):
        genres = [add_genre(session, profile.id, tag) for tag in ("IDM", "braindance", "ambient")]

        move_genre(session, profile.id, genres[2].id, "up")
        assert ordered_tags(profile) == ["IDM", "ambient", "braindance"]

        move_genre(session, profile.id, genres[0].id, "down")
        assert ordered_tags(profile) == ["ambient", "IDM", "braindance"]

    def test_moving_past_the_ends_changes_nothing(self, session, profile):
        first = add_genre(session, profile.id, "IDM")
        last = add_genre(session, profile.id, "ambient")

        move_genre(session, profile.id, first.id, "up")
        move_genre(session, profile.id, last.id, "down")

        assert ordered_tags(profile) == ["IDM", "ambient"]

    def test_invalid_direction_is_rejected(self, session, profile):
        genre = add_genre(session, profile.id, "IDM")

        with pytest.raises(ValueError, match="direction"):
            move_genre(session, profile.id, genre.id, "sideways")

    def test_removing_a_genre_closes_the_gap(self, session, profile):
        genres = [add_genre(session, profile.id, tag) for tag in ("IDM", "braindance", "ambient")]

        remove_genre(session, profile.id, genres[1].id)

        assert ordered_tags(profile) == ["IDM", "ambient"]
        assert [g.priority for g in sorted(profile.genres, key=lambda g: g.priority)] == [0, 1]

    def test_another_profiles_genre_cannot_be_touched(self, session, profile):
        other = create_profile(session, "Side Project")
        genre = add_genre(session, other.id, "IDM")

        with pytest.raises(LookupError):
            remove_genre(session, profile.id, genre.id)


class TestReferenceArtists:
    def test_add_and_remove(self, session, profile):
        artist = add_reference_artist(session, profile.id, " Aphex Twin ")

        assert [a.display_name for a in profile.reference_artists] == ["Aphex Twin"]

        remove_reference_artist(session, profile.id, artist.id)
        assert profile.reference_artists == []

    def test_spelling_variants_count_as_duplicates(self, session, profile):
        add_reference_artist(session, profile.id, "µ-Ziq")

        with pytest.raises(ProfileValidationError) as error:
            add_reference_artist(session, profile.id, "μ-ziq")

        assert "already" in error.value.errors["name"]


class TestAntiSignals:
    def test_artists_and_terms_are_kept_apart(self, session, profile):
        add_anti_signal(session, profile.id, "artist", "Lofi Girl")
        add_anti_signal(session, profile.id, "term", "lofi girl")

        assert {(s.kind, s.value) for s in profile.anti_signals} == {
            ("artist", "Lofi Girl"),
            ("term", "lofi girl"),
        }

    def test_duplicate_within_a_kind_is_rejected(self, session, profile):
        add_anti_signal(session, profile.id, "term", "EDM")

        with pytest.raises(ProfileValidationError) as error:
            add_anti_signal(session, profile.id, "term", " edm ")

        assert "already" in error.value.errors["value"]

    def test_unknown_kind_is_rejected(self, session, profile):
        with pytest.raises(ProfileValidationError) as error:
            add_anti_signal(session, profile.id, "mood", "sad")

        assert "kind" in error.value.errors

    def test_remove(self, session, profile):
        signal = add_anti_signal(session, profile.id, "term", "EDM")

        remove_anti_signal(session, profile.id, signal.id)

        assert profile.anti_signals == []


class TestTracks:
    def test_track_link_is_canonicalised(self, session, profile):
        track = add_track(session, profile.id, "Glass Weather", f"{TRACK}?si=abc123", "Brittle breaks.")

        assert track.spotify_url == TRACK
        assert track.description == "Brittle breaks."

    @pytest.mark.parametrize(
        "url",
        ["https://soundcloud.com/synman/x", "https://open.spotify.com/album/4uLU6hMCjMI75M1A2tKUQC", ""],
    )
    def test_only_spotify_track_links_are_accepted(self, session, profile, url):
        with pytest.raises(ProfileValidationError) as error:
            add_track(session, profile.id, "Glass Weather", url, "")

        assert "open.spotify.com/track" in error.value.errors["spotify_url"]

    def test_title_is_required_and_everything_is_reported(self, session, profile):
        with pytest.raises(ProfileValidationError) as error:
            add_track(session, profile.id, "  ", "nope", "x" * 301)

        assert set(error.value.errors) == {"title", "spotify_url", "description"}

    def test_the_same_track_cannot_be_added_twice(self, session, profile):
        add_track(session, profile.id, "Glass Weather", TRACK, "")

        with pytest.raises(ProfileValidationError) as error:
            add_track(session, profile.id, "Glass Weather (again)", f"{TRACK}?si=zzz", "")

        assert "already" in error.value.errors["spotify_url"]

    def test_remove(self, session, profile):
        track = add_track(session, profile.id, "Glass Weather", TRACK, "")

        remove_track(session, profile.id, track.id)

        assert profile.tracks == []


class TestSearchTerms:
    def test_manual_terms_start_active(self, session, profile):
        term = add_search_term(session, profile.id, "  glitchy   ambient ")

        assert (term.term, term.origin, term.status) == ("glitchy ambient", "manual", "active")

    def test_duplicate_of_any_existing_term_is_rejected(self, session, profile):
        add_search_term(session, profile.id, "glitchy ambient")

        with pytest.raises(ProfileValidationError) as error:
            add_search_term(session, profile.id, "Glitchy Ambient")

        assert "already" in error.value.errors["term"]

    @pytest.mark.parametrize("term", ["", "a", "x" * 201])
    def test_too_short_or_long_terms_are_rejected(self, session, profile, term):
        with pytest.raises(ProfileValidationError) as error:
            add_search_term(session, profile.id, term)

        assert "term" in error.value.errors

    def test_pause_and_resume(self, session, profile):
        term = add_search_term(session, profile.id, "glitchy ambient")

        set_search_term_status(session, profile.id, term.id, "paused")
        assert term.status == SearchTermStatus.PAUSED

        set_search_term_status(session, profile.id, term.id, "active")
        assert term.status == SearchTermStatus.ACTIVE

    def test_only_active_or_paused_can_be_chosen(self, session, profile):
        term = add_search_term(session, profile.id, "glitchy ambient")

        with pytest.raises(ProfileValidationError) as error:
            set_search_term_status(session, profile.id, term.id, "rejected")

        assert "status" in error.value.errors

    def test_unused_term_can_be_deleted(self, session, profile):
        term = add_search_term(session, profile.id, "glitchy ambient")

        remove_search_term(session, profile.id, term.id)

        assert profile.search_terms == []

    def test_term_that_found_playlists_is_kept_for_history(self, session, profile):
        term = add_search_term(session, profile.id, "glitchy ambient")
        session.add(
            PlaylistSource(
                playlist_id=make_playlist(session).spotify_id, provider="serper", search_term_id=term.id
            )
        )
        session.flush()

        with pytest.raises(ProfileValidationError) as error:
            remove_search_term(session, profile.id, term.id)

        assert "pause" in error.value.errors["term"].lower()

    def test_search_requests_per_night_counts_active_terms_only(self, session, profile):
        for text in ("glitchy ambient", "braindance playlist", "IDM playlist"):
            add_search_term(session, profile.id, text)
        set_search_term_status(session, profile.id, profile.search_terms[0].id, "paused")

        assert search_requests_per_night(profile) == 2 * 5


class TestReadinessIsKept:
    def test_removing_something_essential_pauses_an_active_profile(self, session):
        profile = add_contents(session, create_profile(session, "Synman"))
        set_profile_active(session, profile.id, True)

        auto_paused = remove_reference_artist(session, profile.id, profile.reference_artists[0].id)

        assert auto_paused is True
        assert profile.is_active is False

    def test_pausing_a_term_below_the_minimum_pauses_the_profile(self, session):
        profile = add_contents(session, create_profile(session, "Synman"))
        set_profile_active(session, profile.id, True)

        auto_paused = set_search_term_status(session, profile.id, profile.search_terms[0].id, "paused")

        assert auto_paused is True
        assert profile.is_active is False

    def test_removals_that_keep_it_ready_leave_it_active(self, session):
        profile = add_contents(session, create_profile(session, "Synman"), artists=4)
        set_profile_active(session, profile.id, True)

        auto_paused = remove_reference_artist(session, profile.id, profile.reference_artists[0].id)

        assert auto_paused is False
        assert profile.is_active is True
