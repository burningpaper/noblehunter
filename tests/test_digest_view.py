"""What the digest page shows: the night's entries by profile, how to reach each curator, and the counts.

This is read by the web app on Vercel, so it lives in `core` and never touches pipeline code.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from core.contact_routes import best_contact, contact_href
from core.digest_view import digest_view
from core.models import PlaylistProfileFit, PlaylistStatus, Run, RunStageCount
from tests.factories import (
    admin_viewer,
    make_artist,
    make_contact,
    make_curator,
    make_outreach,
    make_playlist,
    make_profile,
    member_viewer,
)

TODAY = date(2026, 9, 15)
YESTERDAY = date(2026, 9, 14)


def entry(
    session, profile, *, digest_date=TODAY, brief="A brief.", angle="An angle.", curator=None, artists=()
):
    curator = curator or make_curator(session, display_name="glitchlists")
    playlist = make_playlist(
        session,
        curator=curator,
        name="Glitch Garden",
        status=PlaylistStatus.DIGESTED,
        followers=1500,
        size_band="500-2k",
        last_add_at=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
    )
    session.add(
        PlaylistProfileFit(
            playlist_id=playlist.spotify_id,
            profile_id=profile.id,
            fit_score=0.9,
            reference_artists_present=list(artists),
            qualified=True,
        )
    )
    session.flush()
    return make_outreach(
        session, curator, profile, digest_date, playlist=playlist, brief_text=brief, suggested_angle=angle
    )


class TestContactLinks:
    @pytest.mark.parametrize(
        ("route", "value", "href"),
        [
            ("email", "demos@glitchlists.net", "mailto:demos@glitchlists.net"),
            ("instagram", "glitchlists", "https://www.instagram.com/glitchlists/"),
            ("x", "glitchlists", "https://x.com/glitchlists"),
            ("bluesky", "glitch.bsky.social", "https://bsky.app/profile/glitch.bsky.social"),
            ("submission-form", "https://forms.gle/Ab12", "https://forms.gle/Ab12"),
            ("other", "linktr.ee/nikdaviesmusic", "https://linktr.ee/nikdaviesmusic"),
        ],
    )
    def test_each_route_becomes_a_link(self, route, value, href):
        assert contact_href(route, value) == href

    @pytest.mark.parametrize("value", ["javascript:alert(1)", "data:text/html,hi", ""])
    def test_nothing_but_web_and_mail_links(self, value):
        assert contact_href("other", value) is None

    def test_the_best_contact_is_the_most_confident_then_the_most_direct(self, session):
        curator = make_curator(session)
        instagram = make_contact(session, curator, route_type="instagram", value="glitchlists")
        email = make_contact(session, curator, route_type="email", value="demos@glitchlists.net")
        email.confidence = "B"
        guess = make_contact(session, curator, route_type="x", value="glitchlists")
        guess.confidence = "C"
        session.flush()

        assert best_contact(session, curator.id).id == instagram.id


class TestDigestView:
    def test_shows_the_latest_digest_by_default(self, session):
        profile = make_profile(session, "Synman")
        entry(session, profile, digest_date=YESTERDAY)
        entry(session, profile, digest_date=TODAY)

        view = digest_view(session, viewer=admin_viewer(), digest_date=None, today=TODAY)

        assert view.digest_date == TODAY
        assert view.earlier_date == YESTERDAY
        assert view.later_date is None

    def test_a_chosen_date_knows_its_neighbours(self, session):
        profile = make_profile(session, "Synman")
        entry(session, profile, digest_date=YESTERDAY)
        entry(session, profile, digest_date=TODAY)

        view = digest_view(session, viewer=admin_viewer(), digest_date=YESTERDAY, today=TODAY)

        assert view.digest_date == YESTERDAY
        assert view.earlier_date is None
        assert view.later_date == TODAY

    def test_entries_are_grouped_by_profile_in_digest_order(self, session):
        synman = make_profile(session, "Synman")
        ambient = make_profile(session, "Ambient")
        first = entry(session, synman)
        second = entry(session, ambient)
        third = entry(session, synman)

        view = digest_view(session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY)

        assert [(group.profile_name, [e.outreach_id for e in group.entries]) for group in view.profiles] == [
            ("Synman", [first.id, third.id]),
            ("Ambient", [second.id]),
        ]
        assert view.total == 3

    def test_an_entry_has_what_a_pitch_needs(self, session):
        profile = make_profile(session, "Synman")
        curator = make_curator(session, display_name="glitchlists")
        make_contact(session, curator, route_type="email", value="demos@glitchlists.net")
        outreach = entry(
            session, profile, curator=curator, brief="Brief text.", angle="Angle text.", artists=("Autechre",)
        )

        shown = (
            digest_view(session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY).profiles[0].entries[0]
        )

        assert shown.outreach_id == outreach.id
        assert shown.status == "new"
        assert shown.playlist_name == "Glitch Garden"
        assert shown.playlist_url == f"https://open.spotify.com/playlist/{outreach.playlist_id}"
        assert shown.curator_name == "glitchlists"
        assert (shown.followers, shown.size_band, shown.days_since_last_add) == (1500, "500-2k", 5)
        assert (shown.route_label, shown.contact_value) == ("Email", "demos@glitchlists.net")
        assert shown.contact_href == "mailto:demos@glitchlists.net"
        assert shown.confidence == "A"
        assert (shown.brief, shown.angle) == ("Brief text.", "Angle text.")
        assert shown.reference_artists == ("Autechre",)

    def test_the_nights_counts_come_from_that_nights_run(self, session):
        profile = make_profile(session, "Synman")
        entry(session, profile)
        run = Run(trigger="schedule", status="succeeded", started_at=datetime(2026, 9, 15, 0, 5, tzinfo=UTC))
        session.add(run)
        session.flush()
        for stage, count_in, count_out, profile_id in [
            ("discover", 120, 80, profile.id),
            ("fetch", 60, 58, None),
            ("qualify", 58, 6, None),
            ("research", 6, 3, None),
            ("digest", 3, 1, None),
        ]:
            session.add(
                RunStageCount(
                    run_id=run.id, profile_id=profile_id, stage=stage, count_in=count_in, count_out=count_out
                )
            )
        session.flush()

        view = digest_view(session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY)

        assert [(count.label, count.count_in, count.count_out) for count in view.counts] == [
            ("Found by search", 120, 80),
            ("Fetched from Spotify", 60, 58),
            ("Qualified", 58, 6),
            ("Contact researched", 6, 3),
            ("In the digest", 3, 1),
        ]

    def test_no_digest_yet(self, session):
        view = digest_view(session, viewer=admin_viewer(), digest_date=None, today=TODAY)

        assert view.digest_date is None
        assert view.profiles == ()
        assert view.total == 0
        assert not view.short

    def test_a_short_digest_is_flagged(self, session):
        profile = make_profile(session, "Synman")
        for _ in range(3):
            entry(session, profile)

        assert digest_view(session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY).short

    def test_a_full_digest_is_not(self, session):
        profile = make_profile(session, "Synman")
        for _ in range(10):
            entry(session, profile)

        assert not digest_view(session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY).short

    def test_days_since_last_add_are_counted_from_today(self, session):
        entry(session, make_profile(session, "Synman"))

        later = (
            digest_view(session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY + timedelta(days=2))
            .profiles[0]
            .entries[0]
        )

        assert later.days_since_last_add == 7


class TestProfileFilter:
    """Narrowing a mixed digest to one profile, the way the Inbox's filter does."""

    def test_it_shows_only_the_chosen_profiles_entries(self, session):
        synman = make_profile(session, "Synman")
        newire = make_profile(session, "NEWIRE")
        mine = entry(session, synman)
        entry(session, newire)

        view = digest_view(
            session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY, profile_id=synman.id
        )

        assert [(g.profile_name, [e.outreach_id for e in g.entries]) for g in view.profiles] == [
            ("Synman", [mine.id])
        ]
        assert view.chosen_profile_id == synman.id

    def test_without_a_filter_every_visible_profile_is_still_shown(self, session):
        synman = make_profile(session, "Synman")
        newire = make_profile(session, "NEWIRE")
        entry(session, synman)
        entry(session, newire)

        view = digest_view(session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY)

        assert {group.profile_name for group in view.profiles} == {"Synman", "NEWIRE"}
        assert view.chosen_profile_id is None

    def test_the_filter_offers_every_profile_with_entries_including_the_filtered_out_one(self, session):
        synman = make_profile(session, "Synman")
        newire = make_profile(session, "NEWIRE")
        entry(session, synman)
        entry(session, newire, digest_date=YESTERDAY)  # another night: still selectable

        view = digest_view(
            session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY, profile_id=synman.id
        )

        assert view.filter_profiles == ((newire.id, "NEWIRE"), (synman.id, "Synman"))

    def test_a_member_is_never_offered_another_artists_profile(self, session):
        mine = make_artist(session, "Mine")
        theirs = make_artist(session, "Theirs")
        entry(session, make_profile(session, "Synman", artist=mine))
        entry(session, make_profile(session, "Theirs Profile", artist=theirs))

        view = digest_view(session, viewer=member_viewer(mine), digest_date=TODAY, today=TODAY)

        assert [name for _id, name in view.filter_profiles] == ["Synman"]

    def test_the_dates_offered_are_the_chosen_profiles_own(self, session):
        synman = make_profile(session, "Synman")
        newire = make_profile(session, "NEWIRE")
        entry(session, synman, digest_date=TODAY)
        entry(session, newire, digest_date=YESTERDAY)

        filtered = digest_view(
            session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY, profile_id=synman.id
        )
        unfiltered = digest_view(session, viewer=admin_viewer(), digest_date=TODAY, today=TODAY)

        # Stepping back to a night only NEWIRE ran would land on an empty page, so it isn't offered.
        assert filtered.earlier_date is None
        assert unfiltered.earlier_date == YESTERDAY

    def test_the_latest_under_a_filter_is_that_profiles_own_latest_night(self, session):
        synman = make_profile(session, "Synman")
        newire = make_profile(session, "NEWIRE")
        entry(session, synman, digest_date=YESTERDAY)
        entry(session, newire, digest_date=TODAY)

        view = digest_view(
            session, viewer=admin_viewer(), digest_date=None, today=TODAY, profile_id=synman.id
        )

        assert view.digest_date == YESTERDAY
        assert view.total == 1

    def test_a_night_the_chosen_profile_missed_is_empty_but_keeps_the_filter(self, session):
        synman = make_profile(session, "Synman")
        newire = make_profile(session, "NEWIRE")
        entry(session, synman, digest_date=TODAY)
        entry(session, newire, digest_date=YESTERDAY)

        view = digest_view(
            session, viewer=admin_viewer(), digest_date=YESTERDAY, today=TODAY, profile_id=synman.id
        )

        assert view.profiles == ()
        assert view.total == 0
        assert view.chosen_profile_id == synman.id
        assert len(view.filter_profiles) == 2  # still a way back to everything
