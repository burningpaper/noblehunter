"""Exclusion rules: who and what must not reach the digest again, and for how long.

Decisions (2026-09-13): a curator appears at most once per 90 days across all profiles;
`pitched` and `skip` wait out the 90 days; `bad-fit` and `dead` exclude permanently.
`no-contact`, not-alive and no-fit playlists are re-checked after 90 days; pay-to-play
and bot (`not-real`) rejections are permanent.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from core.exclusion import contact_is_excluded, curator_is_eligible, playlist_ids_to_skip, record_verdict
from core.models import PlaylistStatus, RejectionReason
from tests.factories import make_contact, make_curator, make_outreach, make_playlist, make_profile

TODAY = date(2026, 9, 14)


def at(day: date) -> datetime:
    return datetime.combine(day, time(2, 0), tzinfo=UTC)


class TestCuratorEligibility:
    def test_new_curator_is_eligible(self, session):
        assert curator_is_eligible(session, make_curator(session).id, TODAY)

    def test_curator_digested_30_days_ago_is_not_eligible(self, session):
        curator = make_curator(session)
        make_outreach(session, curator, make_profile(session), TODAY - timedelta(days=30))

        assert not curator_is_eligible(session, curator.id, TODAY)

    def test_curator_digested_exactly_90_days_ago_is_eligible(self, session):
        curator = make_curator(session)
        make_outreach(session, curator, make_profile(session), TODAY - timedelta(days=90))

        assert curator_is_eligible(session, curator.id, TODAY)

    def test_excluded_curator_is_never_eligible(self, session):
        curator = make_curator(
            session, excluded_at=at(TODAY - timedelta(days=400)), exclusion_reason="bad-fit"
        )

        assert not curator_is_eligible(session, curator.id, TODAY)


class TestRecordVerdict:
    def test_pitched_records_status_and_time(self, session):
        outreach = make_outreach(session, make_curator(session), make_profile(session), TODAY)

        record_verdict(session, outreach.id, "pitched", at(TODAY))

        assert outreach.status == "pitched"
        assert outreach.pitched_at == at(TODAY)
        assert outreach.status_changed_at == at(TODAY)

    def test_skip_leaves_curator_eligible_after_90_days(self, session):
        curator = make_curator(session)
        outreach = make_outreach(session, curator, make_profile(session), TODAY)

        record_verdict(session, outreach.id, "skip", at(TODAY))

        assert curator.excluded_at is None
        assert curator_is_eligible(session, curator.id, TODAY + timedelta(days=90))

    @pytest.mark.parametrize("verdict", ["bad-fit", "dead"])
    def test_bad_fit_and_dead_exclude_curator_permanently(self, session, verdict):
        curator = make_curator(session)
        outreach = make_outreach(session, curator, make_profile(session), TODAY)

        record_verdict(session, outreach.id, verdict, at(TODAY))

        assert curator.excluded_at == at(TODAY)
        assert curator.exclusion_reason == verdict
        assert not curator_is_eligible(session, curator.id, TODAY + timedelta(days=1000))

    def test_unknown_verdict_raises(self, session):
        outreach = make_outreach(session, make_curator(session), make_profile(session), TODAY)

        with pytest.raises(ValueError, match="Unknown verdict"):
            record_verdict(session, outreach.id, "maybe", at(TODAY))

    def test_missing_outreach_raises(self, session):
        with pytest.raises(LookupError, match="999999"):
            record_verdict(session, 999999, "skip", at(TODAY))


class TestPlaylistsToSkip:
    def test_unknown_playlist_is_not_skipped(self, session):
        assert playlist_ids_to_skip(session, ["9" * 22], TODAY) == set()

    @pytest.mark.parametrize("status", [PlaylistStatus.CANDIDATE, PlaylistStatus.QUALIFIED])
    def test_in_progress_playlists_are_skipped(self, session, status):
        playlist = make_playlist(session, status=status)

        assert playlist_ids_to_skip(session, [playlist.spotify_id], TODAY) == {playlist.spotify_id}

    @pytest.mark.parametrize(
        "status, reason",
        [
            (PlaylistStatus.NO_CONTACT, None),
            (PlaylistStatus.DIGESTED, None),
            (PlaylistStatus.REJECTED, RejectionReason.NOT_ALIVE),
            (PlaylistStatus.REJECTED, RejectionReason.NO_FIT),
        ],
    )
    def test_recheckable_playlists_skipped_within_90_days(self, session, status, reason):
        playlist = make_playlist(
            session, status=status, rejection_reason=reason, last_checked_at=at(TODAY - timedelta(days=89))
        )

        assert playlist_ids_to_skip(session, [playlist.spotify_id], TODAY) == {playlist.spotify_id}

    @pytest.mark.parametrize(
        "status, reason",
        [
            (PlaylistStatus.NO_CONTACT, None),
            (PlaylistStatus.DIGESTED, None),
            (PlaylistStatus.REJECTED, RejectionReason.NOT_ALIVE),
            (PlaylistStatus.REJECTED, RejectionReason.NO_FIT),
        ],
    )
    def test_recheckable_playlists_come_back_after_90_days(self, session, status, reason):
        playlist = make_playlist(
            session, status=status, rejection_reason=reason, last_checked_at=at(TODAY - timedelta(days=90))
        )

        assert playlist_ids_to_skip(session, [playlist.spotify_id], TODAY) == set()

    def test_pay_to_play_rejection_is_permanent(self, session):
        playlist = make_playlist(
            session,
            status=PlaylistStatus.REJECTED,
            rejection_reason=RejectionReason.NOT_REAL,
            last_checked_at=at(TODAY - timedelta(days=1000)),
        )

        assert playlist_ids_to_skip(session, [playlist.spotify_id], TODAY) == {playlist.spotify_id}

    def test_fetch_failed_playlists_are_retried(self, session):
        playlist = make_playlist(session, status=PlaylistStatus.FETCH_FAILED)

        assert playlist_ids_to_skip(session, [playlist.spotify_id], TODAY) == set()

    def test_only_requested_ids_are_returned(self, session):
        skipped = make_playlist(session, status=PlaylistStatus.CANDIDATE)
        make_playlist(session, status=PlaylistStatus.CANDIDATE)

        assert playlist_ids_to_skip(session, [skipped.spotify_id, "9" * 22], TODAY) == {skipped.spotify_id}


class TestContactExclusion:
    def test_unknown_contact_is_not_excluded(self, session):
        assert not contact_is_excluded(session, "email:new@label.com", None, TODAY)

    def test_contact_of_recently_digested_curator_is_excluded(self, session):
        curator = make_curator(session)
        contact = make_contact(session, curator, value="curator@label.com")
        make_outreach(session, curator, make_profile(session), TODAY - timedelta(days=10))

        assert contact_is_excluded(session, contact.contact_key, None, TODAY)

    def test_contact_of_curator_digested_long_ago_is_not_excluded(self, session):
        curator = make_curator(session)
        contact = make_contact(session, curator, value="curator@label.com")
        make_outreach(session, curator, make_profile(session), TODAY - timedelta(days=100))

        assert not contact_is_excluded(session, contact.contact_key, None, TODAY)

    def test_contact_of_permanently_excluded_curator_is_excluded(self, session):
        curator = make_curator(session, excluded_at=at(TODAY - timedelta(days=500)), exclusion_reason="dead")
        contact = make_contact(session, curator, value="curator@label.com")

        assert contact_is_excluded(session, contact.contact_key, None, TODAY)

    def test_new_address_at_an_excluded_curators_company_domain_is_excluded(self, session):
        curator = make_curator(session, excluded_at=at(TODAY), exclusion_reason="bad-fit")
        make_contact(session, curator, value="promo@coollabel.com")

        assert contact_is_excluded(session, "email:someone-else@coollabel.com", "domain:coollabel.com", TODAY)

    def test_domain_of_eligible_curator_does_not_exclude(self, session):
        make_contact(session, make_curator(session), value="promo@coollabel.com")

        assert not contact_is_excluded(session, "email:other@coollabel.com", "domain:coollabel.com", TODAY)
