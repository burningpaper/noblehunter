"""Stage 5b: a per-profile follower floor. Under 50 followers (by default) isn't worth a pitch.

Too-small playlists are rejected with their own reason, which is re-checked after 90 days
because small playlists grow. A playlist too small for one profile can still qualify for
another profile with a lower floor.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from core.exclusion import playlist_ids_to_skip
from core.models import Profile, RejectionReason
from core.profiles import ProfileValidationError, create_profile, update_profile_settings
from pipeline.qualify import ProfileRules, assess_fit, judge
from pipeline.spotify import PlaylistData, Track
from tests.factories import default_artist_id, make_playlist
from tests.web_helpers import FakeGoogle, csrf_token, sign_in, web_settings
from web.app import create_app
from web.db import get_db

TODAY = date(2026, 9, 14)
REFERENCE_ARTISTS = ("Aphex Twin", "Autechre", "Plaid")


def added(days_ago: int) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_ago), time(12, 0), tzinfo=UTC)


def playlist(followers: int | None) -> PlaylistData:
    tracks = tuple(
        Track(uid=str(i), name="t", artists=(artist,), added_at=added(days))
        for i, (artist, days) in enumerate(zip(REFERENCE_ARTISTS, (3, 30, 90), strict=True))
    )
    return PlaylistData(
        spotify_id="0" * 22,
        name="Late Night Machines",
        description_html="",
        description_text="",
        owner_id="curator1",
        owner_name="A Curator",
        followers=followers,
        total_tracks=60,
        cover_image_url=None,
        tracks=tracks,
        partial=False,
    )


def rules(profile_id: int = 1, min_followers: int = 50, **overrides) -> ProfileRules:
    return ProfileRules(
        profile_id=profile_id,
        name=f"Profile {profile_id}",
        reference_artists=REFERENCE_ARTISTS,
        min_followers=min_followers,
        **overrides,
    )


class TestFit:
    def test_rules_default_to_a_floor_of_50(self):
        assert ProfileRules(profile_id=1, name="Synman").min_followers == 50

    def test_a_great_fit_under_the_floor_is_too_small(self):
        fit = assess_fit(playlist(followers=30), rules())

        assert fit.tier == "too-small"
        assert fit.qualifies is False
        assert fit.reference_artists_present == ("Aphex Twin", "Autechre", "Plaid")

    def test_exactly_the_floor_is_enough(self):
        assert assess_fit(playlist(followers=50), rules()).tier == "top"

    def test_a_floor_of_zero_means_no_floor(self):
        assert assess_fit(playlist(followers=0), rules(min_followers=0)).tier == "top"

    def test_unknown_follower_count_counts_as_too_small(self):
        assert assess_fit(playlist(followers=None), rules()).tier == "too-small"

    def test_anti_signals_still_win_over_size(self):
        tight = rules(anti_terms=("late night",))

        assert assess_fit(playlist(followers=10), tight).tier == "rejected"


class TestJudge:
    def test_fitting_but_too_small_everywhere_is_rejected_as_too_small(self):
        verdict = judge(playlist(followers=20), [rules()], TODAY)

        assert (verdict.status, verdict.rejection_reason) == ("rejected", "too-small")

    def test_too_small_for_one_profile_can_qualify_for_another(self):
        verdict = judge(
            playlist(followers=20), [rules(1, min_followers=50), rules(2, min_followers=10)], TODAY
        )

        assert verdict.status == "qualified"
        assert verdict.qualified_profile_ids == (2,)

    def test_no_fit_beats_too_small_when_nothing_fits(self):
        off_genre = ProfileRules(profile_id=1, name="Other", reference_artists=("Nobody", "Nope", "Nada"))

        assert judge(playlist(followers=20), [off_genre], TODAY).rejection_reason == "no-fit"


class TestProfileSetting:
    def test_new_profiles_start_with_a_floor_of_50(self, session):
        assert create_profile(session, default_artist_id(session), "Synman").min_followers == 50

    def test_rules_are_built_from_the_profile_setting(self, session):
        profile = create_profile(session, default_artist_id(session), "Synman")
        update_profile_settings(session, profile.id, "Synman", "20", min_followers="120")

        assert ProfileRules.from_profile(profile).min_followers == 120

    @pytest.mark.parametrize("value", ["-1", "abc", "1.5", "1000001"])
    def test_invalid_floors_are_rejected(self, session, value):
        profile = create_profile(session, default_artist_id(session), "Synman")

        with pytest.raises(ProfileValidationError) as error:
            update_profile_settings(session, profile.id, "Synman", "20", min_followers=value)

        assert "min_followers" in error.value.errors

    def test_leaving_the_floor_out_keeps_it(self, session):
        profile = create_profile(session, default_artist_id(session), "Synman")
        update_profile_settings(session, profile.id, "Synman", "20", min_followers="75")

        update_profile_settings(session, profile.id, "Synman Live", "20")

        assert profile.min_followers == 75

    def test_the_database_refuses_a_negative_floor(self, session):
        session.add(Profile(name="Broken", min_followers=-5, artist_id=default_artist_id(session)))

        with pytest.raises(IntegrityError):
            session.flush()


class TestRejectionReason:
    def test_too_small_is_a_valid_reason(self, session):
        stored = make_playlist(session, status="rejected", rejection_reason=RejectionReason.TOO_SMALL)

        assert stored.rejection_reason == "too-small"

    def test_too_small_playlists_are_rechecked_after_90_days(self, session):
        recent = make_playlist(
            session, status="rejected", rejection_reason="too-small", last_checked_at=added(10)
        )
        old = make_playlist(
            session, status="rejected", rejection_reason="too-small", last_checked_at=added(95)
        )

        assert playlist_ids_to_skip(session, [recent.spotify_id, old.spotify_id], TODAY) == {
            recent.spotify_id
        }


class TestWebSetting:
    @pytest.fixture
    def client(self, session):
        app = create_app(web_settings(), identity_provider=FakeGoogle())

        def use_test_session():
            yield session

        app.dependency_overrides[get_db] = use_test_session
        test_client = TestClient(app, follow_redirects=False)
        sign_in(test_client)
        return test_client

    def post_settings(self, client, profile_id, data):
        headers = {"x-csrf-token": csrf_token(client), "hx-request": "true"}
        return client.post(f"/profiles/{profile_id}/settings", data=data, headers=headers)

    def test_the_settings_form_shows_the_floor(self, client, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        html = client.get(f"/profiles/{profile.id}", headers={"accept": "text/html"}).text

        assert 'name="min_followers"' in html
        assert 'value="50"' in html

    def test_saving_a_new_floor(self, client, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        response = self.post_settings(
            client, profile.id, {"name": "Synman", "digest_target": "20", "min_followers": "200"}
        )

        assert response.status_code == 200
        session.refresh(profile)
        assert profile.min_followers == 200

    def test_an_invalid_floor_is_explained(self, client, session):
        profile = create_profile(session, default_artist_id(session), "Synman")

        response = self.post_settings(
            client, profile.id, {"name": "Synman", "digest_target": "20", "min_followers": "lots"}
        )

        assert response.status_code == 422
        assert "followers" in response.text.lower()
