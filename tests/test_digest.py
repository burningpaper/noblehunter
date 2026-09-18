"""The digest: the night's best reachable curators, one each, with a brief ready to pitch from.

Ranking is the spec's weighted sum (fit first, then contact confidence, how recently the
playlist moved, and size). A curator appears once, under the profile they fit best, and each
profile takes at most its digest target. Every entry becomes an `outreach` row with status
`new`; if Claude can't write the brief, a plain one is used so the digest still ships.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import select

from core.models import (
    MailDirection,
    Outreach,
    PlaylistProfileFit,
    PlaylistStatus,
    ProfileTrack,
    RunStageCount,
)
from pipeline.digest import Brief, build_digest
from pipeline.qualify import size_band
from pipeline.runs import start_run
from tests.conversation_helpers import open_conversation, pitched
from tests.factories import make_contact, make_curator, make_outreach, make_playlist, make_profile

TODAY = date(2026, 9, 15)
NOW = datetime(2026, 9, 15, 4, 0, tzinfo=UTC)
_handles = count(1)


def active_profile(session, name: str = "Synman", digest_target: int = 20):
    profile = make_profile(session, name)
    profile.is_active = True
    profile.digest_target = digest_target
    session.add(
        ProfileTrack(
            profile=profile,
            title="Glass Weather",
            spotify_url=f"https://open.spotify.com/track/{next(_handles):022d}",
            description="Brittle breaks over warm pads",
        )
    )
    session.flush()
    return profile


def ready_lead(
    session,
    profile,
    *,
    fit: float = 0.8,
    grade: str | None = "A",
    route: str = "email",
    curator=None,
    followers: int = 1500,
    days_since_add: int = 5,
    description: str = "Our favourite IDM",
):
    curator = curator or make_curator(session)
    playlist = make_playlist(
        session,
        curator=curator,
        status=PlaylistStatus.QUALIFIED,
        followers=followers,
        size_band=size_band(followers),
        last_add_at=NOW - timedelta(days=days_since_add),
        description=description,
    )
    session.add(
        PlaylistProfileFit(
            playlist_id=playlist.spotify_id,
            profile_id=profile.id,
            fit_score=fit,
            reference_artists_present=["Autechre", "Boards of Canada"],
            qualified=True,
        )
    )
    if grade is not None:
        value = f"curator_{next(_handles)}" if route != "email" else None
        contact = make_contact(session, curator, route_type=route, value=value)
        contact.confidence = grade
    session.flush()
    return playlist


class FakeWriter:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.requests: list = []

    def write(self, request) -> Brief:
        self.requests.append(request)
        if self.error:
            raise self.error
        return Brief(
            text=f"Brief for {request.playlist_name}", angle="Lead with the breaks", spend_usd=Decimal("0.01")
        )


PLENTY = Decimal("10.00")


def digest_tonight(session, writer=None, cap: Decimal = PLENTY):
    run = start_run(session)
    summary = build_digest(
        session,
        writer=writer if writer is not None else FakeWriter(),
        run=run,
        today=TODAY,
        now=NOW,
        spend_cap_usd=cap,
    )
    return run, summary


def entries_today(session) -> list[Outreach]:
    return session.scalars(select(Outreach).where(Outreach.digest_date == TODAY).order_by(Outreach.id)).all()


class TestChoosing:
    def test_better_fit_comes_first(self, session):
        profile = active_profile(session, digest_target=1)
        ready_lead(session, profile, fit=0.3)
        strong = ready_lead(session, profile, fit=0.9)

        digest_tonight(session)

        assert [entry.playlist_id for entry in entries_today(session)] == [strong.spotify_id]

    def test_with_equal_fit_a_curator_stated_contact_wins(self, session):
        profile = active_profile(session, digest_target=1)
        ready_lead(session, profile, fit=0.8, grade="B")
        stated = ready_lead(session, profile, fit=0.8, grade="A")

        digest_tonight(session)

        assert [entry.playlist_id for entry in entries_today(session)] == [stated.spotify_id]

    def test_only_curators_with_a_real_contact_make_it(self, session):
        profile = active_profile(session)
        ready_lead(session, profile, grade="C")
        ready_lead(session, profile, grade=None)
        reachable = ready_lead(session, profile)

        digest_tonight(session)

        assert [entry.playlist_id for entry in entries_today(session)] == [reachable.spotify_id]

    def test_a_curator_appears_once_under_the_profile_they_fit_best(self, session):
        synman = active_profile(session, "Synman")
        ambient = active_profile(session, "Ambient")
        curator = make_curator(session)
        ready_lead(session, ambient, fit=0.4, curator=curator)
        ready_lead(session, synman, fit=0.9, curator=curator)

        digest_tonight(session)

        assert [entry.profile_id for entry in entries_today(session)] == [synman.id]

    def test_each_profile_takes_at_most_its_digest_target(self, session):
        profile = active_profile(session, digest_target=2)
        for fit in (0.9, 0.8, 0.7):
            ready_lead(session, profile, fit=fit)

        digest_tonight(session)

        assert len(entries_today(session)) == 2

    def test_curators_on_cooldown_or_excluded_are_left_out(self, session):
        profile = active_profile(session)
        recent = make_curator(session)
        make_outreach(session, recent, profile, TODAY - timedelta(days=30))
        ready_lead(session, profile, curator=recent)
        ready_lead(
            session, profile, curator=make_curator(session, excluded_at=NOW, exclusion_reason="bad-fit")
        )

        digest_tonight(session)

        assert entries_today(session) == []

    def test_paused_profiles_are_left_out(self, session):
        ready_lead(session, make_profile(session, "Paused"))

        digest_tonight(session)

        assert entries_today(session) == []


class TestWriting:
    def test_an_entry_records_the_brief_and_marks_the_playlist_digested(self, session):
        profile = active_profile(session)
        lead = ready_lead(session, profile)

        run, summary = digest_tonight(session)

        entry = entries_today(session)[0]
        assert entry.status == "new"
        assert entry.brief_text == f"Brief for {lead.name}"
        assert entry.suggested_angle == "Lead with the breaks"
        assert (entry.curator_id, entry.profile_id) == (lead.curator_id, profile.id)
        assert lead.status == PlaylistStatus.DIGESTED
        assert summary.entries == 1
        assert run.llm_spend_usd == Decimal("0.01")

    def test_the_brief_writer_gets_what_a_good_brief_needs(self, session):
        profile = active_profile(session)
        lead = ready_lead(session, profile, description="Send one track only, no attachments.")
        writer = FakeWriter()

        digest_tonight(session, writer)

        request = writer.requests[0]
        assert request.profile_name == "Synman"
        assert request.playlist_name == lead.name
        assert request.playlist_url == f"https://open.spotify.com/playlist/{lead.spotify_id}"
        assert request.description == "Send one track only, no attachments."
        assert request.reference_artists == ("Autechre", "Boards of Canada")
        assert request.tracks == (("Glass Weather", "Brittle breaks over warm pads"),)
        assert request.contact_route_type == "email"
        assert request.contact_value.endswith("@example-label.com")
        assert request.followers == 1500
        assert request.days_since_last_add == 5

    def test_an_email_is_offered_before_a_social_profile(self, session):
        profile = active_profile(session)
        curator = make_curator(session)
        ready_lead(session, profile, curator=curator, route="instagram")
        make_contact(session, curator, route_type="email")
        writer = FakeWriter()

        digest_tonight(session, writer)

        assert writer.requests[0].contact_route_type == "email"

    def test_when_claude_cannot_write_the_brief_a_plain_one_is_used(self, session):
        profile = active_profile(session)
        lead = ready_lead(session, profile)

        _, summary = digest_tonight(session, FakeWriter(error=RuntimeError("overloaded")))

        entry = entries_today(session)[0]
        assert lead.name in entry.brief_text
        assert "Autechre" in entry.brief_text
        assert summary.template_briefs == 1
        assert any("overloaded" in error for error in summary.errors)

    def test_without_a_writer_every_brief_is_plain(self, session):
        ready_lead(session, active_profile(session))
        run = start_run(session)

        summary = build_digest(session, writer=None, run=run, today=TODAY, now=NOW, spend_cap_usd=PLENTY)

        assert summary.template_briefs == 1
        assert len(entries_today(session)) == 1


class TestBudget:
    """Briefs stop at the nightly cap, but a spent budget never costs a lead (run 8, 2026-09-18)."""

    def leads(self, session, count: int):
        profile = active_profile(session, digest_target=count)
        for _ in range(count):
            ready_lead(session, profile)
        return profile

    def test_with_money_to_spare_claude_writes_every_brief(self, session):
        self.leads(session, 3)
        writer = FakeWriter()

        _, summary = digest_tonight(session, writer)

        assert len(writer.requests) == 3
        assert len(entries_today(session)) == 3
        assert (summary.budget_briefs, summary.template_briefs) == (0, 0)

    def test_a_spent_budget_still_hands_over_every_lead(self, session):
        self.leads(session, 3)
        writer = FakeWriter()

        run, summary = digest_tonight(session, writer, cap=Decimal(0))

        assert writer.requests == []
        entries = entries_today(session)
        assert len(entries) == 3
        assert all("Autechre" in entry.brief_text for entry in entries)
        assert (summary.entries, summary.budget_briefs, summary.template_briefs) == (3, 3, 0)
        assert (summary.spend_usd, run.llm_spend_usd) == (Decimal(0), Decimal(0))

    def test_when_the_money_runs_out_partway_the_later_briefs_are_plain(self, session):
        self.leads(session, 3)
        writer = FakeWriter()

        # Two briefs at a cent each reach a two-cent cap, so the third is written from the facts.
        _, summary = digest_tonight(session, writer, cap=Decimal("0.02"))

        assert len(writer.requests) == 2
        entries = entries_today(session)
        assert len(entries) == 3
        assert sum("Autechre" in entry.brief_text for entry in entries) == 1
        assert (summary.budget_briefs, summary.template_briefs) == (1, 0)
        assert summary.spend_usd == Decimal("0.02")

    def test_a_brief_claude_fumbled_is_not_counted_against_the_budget(self, session):
        self.leads(session, 1)

        _, summary = digest_tonight(session, FakeWriter(error=RuntimeError("overloaded")))

        assert (summary.template_briefs, summary.budget_briefs) == (1, 0)
        assert len(entries_today(session)) == 1


class TestRunningAgain:
    def test_running_twice_in_a_day_adds_nothing_new(self, session):
        profile = active_profile(session, digest_target=2)
        ready_lead(session, profile, fit=0.9)
        ready_lead(session, profile, fit=0.8)

        digest_tonight(session)
        digest_tonight(session)

        assert len(entries_today(session)) == 2

    def test_a_later_run_the_same_day_only_tops_up(self, session):
        profile = active_profile(session, digest_target=2)
        ready_lead(session, profile, fit=0.9)
        digest_tonight(session)
        ready_lead(session, profile, fit=0.5)
        ready_lead(session, profile, fit=0.4)

        digest_tonight(session)

        assert len(entries_today(session)) == 2

    def test_stage_counts_are_recorded(self, session):
        profile = active_profile(session, digest_target=1)
        ready_lead(session, profile, fit=0.9)
        ready_lead(session, profile, fit=0.8)

        run, _ = digest_tonight(session)

        row = session.scalars(
            select(RunStageCount).where(RunStageCount.run_id == run.id, RunStageCount.stage == "digest")
        ).one()
        assert (row.count_in, row.count_out) == (2, 1)


@pytest.mark.parametrize("days", [0, 30, 59])
def test_recent_activity_ranks_above_stale_activity(session, days):
    profile = active_profile(session, digest_target=1)
    ready_lead(session, profile, fit=0.8, days_since_add=days + 1)
    fresher = ready_lead(session, profile, fit=0.8, days_since_add=days)

    digest_tonight(session)

    assert [entry.playlist_id for entry in entries_today(session)] == [fresher.spotify_id]


class TestConversationAllowance:
    """The ceiling, not the target, decides how many leads a profile gets (Jarred, 2026-09-16)."""

    def test_an_empty_profile_takes_its_whole_target(self, session):
        profile = active_profile(session, digest_target=3)
        for _ in range(5):
            ready_lead(session, profile)

        digest_tonight(session)

        assert len(entries_today(session)) == 3

    def test_open_conversations_take_room_away(self, session):
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit = 6
        for _ in range(4):
            open_conversation(session, profile, now=NOW)
        for _ in range(5):
            ready_lead(session, profile)

        digest_tonight(session)

        assert len(entries_today(session)) == 2

    def test_a_full_profile_gets_nothing_tonight(self, session):
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit = 2
        for _ in range(2):
            open_conversation(session, profile, now=NOW)
        ready_lead(session, profile)

        digest_tonight(session)

        assert entries_today(session) == []

    def test_quiet_conversations_give_the_room_back(self, session):
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit, profile.quiet_after_days = 2, 14
        open_conversation(session, profile, now=NOW, days_ago=40)
        open_conversation(session, profile, now=NOW, days_ago=40)
        for _ in range(3):
            ready_lead(session, profile)

        digest_tonight(session)

        assert len(entries_today(session)) == 2

    def test_a_reply_keeps_taking_up_room_however_old(self, session):
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit, profile.quiet_after_days = 1, 14
        pitched(
            session,
            profile,
            messages=[(MailDirection.OUT, 60), (MailDirection.IN, 50)],
            now=NOW,
        )
        ready_lead(session, profile)

        digest_tonight(session)

        assert entries_today(session) == []

    def test_one_profile_being_full_doesnt_starve_another(self, session):
        full = active_profile(session, name="Full", digest_target=5)
        full.open_conversation_limit = 1
        open_conversation(session, full, now=NOW)
        ready_lead(session, full)
        free = active_profile(session, name="Free", digest_target=2)
        for _ in range(3):
            ready_lead(session, free)

        digest_tonight(session)

        assert [entry.profile_id for entry in entries_today(session)] == [free.id, free.id]
