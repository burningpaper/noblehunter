"""Contact research: find the human behind a qualified playlist, and never overstate the evidence.

The free steps (the description, then its links) run first. Claude is asked only when they find
nothing, and whatever it claims is checked against what the tools actually saw. The nightly loop
spends the budget on the playlists most likely to reach the digest.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.models import Contact, PlaylistProfileFit, PlaylistStatus, RunStageCount
from pipeline.research import (
    MAX_LINKS_FOLLOWED,
    AgentReport,
    Budget,
    ClaimedContact,
    ContactFinding,
    Evidence,
    Lead,
    ResearchResult,
    record_research,
    research_lead,
    run_research,
    verify_claims,
)
from pipeline.runs import start_run
from pipeline.web import FetchError, Page
from tests.conversation_helpers import open_conversation
from tests.factories import make_contact, make_curator, make_outreach, make_playlist, make_profile

TODAY = date(2026, 9, 14)
NOW = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)
PLAYLIST_ID = "0" * 22
PLAYLIST_URL = f"https://open.spotify.com/playlist/{PLAYLIST_ID}"


def a_lead(description: str = "", **overrides) -> Lead:
    fields = {
        "playlist_id": PLAYLIST_ID,
        "playlist_name": "Glitch Garden",
        "playlist_url": PLAYLIST_URL,
        "description": description,
        "curator_name": "glitchlists",
        "owner_spotify_id": "glitchlists",
        "reference_artists": ("Autechre",),
    }
    return Lead(**{**fields, **overrides})


def a_page(url: str, text: str = "", links=()) -> Page:
    return Page(url=url, title="", text=text, links=tuple(links))


class FakePages:
    def __init__(self, pages: dict | None = None):
        self.pages = pages or {}
        self.requested: list[str] = []

    def fetch(self, url: str) -> Page:
        self.requested.append(url)
        answer = self.pages.get(url)
        if answer is None:
            raise FetchError("http", "HTTP 404")
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeAgent:
    def __init__(self, report: AgentReport | None = None, error: Exception | None = None):
        self.report = report or AgentReport()
        self.error = error
        self.leads: list[Lead] = []

    def investigate(self, lead: Lead, budget: Budget) -> AgentReport:
        self.leads.append(lead)
        if self.error:
            raise self.error
        return self.report


def routes(result) -> list[tuple[str, str, str, str]]:
    return [(c.route_type, c.value, c.confidence, c.source_url) for c in result.contacts]


class TestFreeSteps:
    def test_an_email_in_the_description_is_grade_a_and_needs_nothing_else(self):
        pages, agent = FakePages(), FakeAgent()

        result = research_lead(
            a_lead("Submissions: demos@glitchlists.net"), pages=pages, agent=agent, budget=Budget()
        )

        assert routes(result) == [("email", "demos@glitchlists.net", "A", PLAYLIST_URL)]
        assert pages.requested == []
        assert agent.leads == []
        assert result.reachable
        assert not result.used_agent

    def test_links_in_the_description_are_followed_for_contacts(self):
        linktree = a_page(
            "https://linktr.ee/glitchlists",
            "Curating IDM since 2019",
            ["https://www.instagram.com/glitchlists/", "mailto:hello@glitchlists.net"],
        )
        pages = FakePages({"https://linktr.ee/glitchlists": linktree})

        result = research_lead(
            a_lead("all my links: linktr.ee/glitchlists"), pages=pages, agent=FakeAgent(), budget=Budget()
        )

        assert set(routes(result)) == {
            ("instagram", "glitchlists", "A", "https://linktr.ee/glitchlists"),
            ("email", "hello@glitchlists.net", "A", "https://linktr.ee/glitchlists"),
        }
        assert result.fetches == 1

    def test_a_dead_link_just_moves_on_to_the_agent(self):
        agent = FakeAgent()

        research_lead(a_lead("site: glitchlists.net"), pages=FakePages(), agent=agent, budget=Budget())

        assert len(agent.leads) == 1

    def test_only_the_first_few_links_are_followed(self):
        description = " ".join(f"https://site{i}.com" for i in range(MAX_LINKS_FOLLOWED + 3))
        pages = FakePages()

        research_lead(a_lead(description), pages=pages, agent=None, budget=Budget())

        assert len(pages.requested) == MAX_LINKS_FOLLOWED

    def test_following_links_respects_the_fetch_budget(self):
        pages = FakePages()

        research_lead(
            a_lead("https://a.com and https://b.com"), pages=pages, agent=None, budget=Budget(max_fetches=1)
        )

        assert pages.requested == ["https://a.com"]


class TestAgentStep:
    def test_the_agent_is_asked_only_when_the_free_steps_find_nothing(self):
        report = AgentReport(
            contacts=(
                ClaimedContact(
                    "instagram", "glitchlists", "B", "https://www.instagram.com/glitchlists/", "Bio"
                ),
            ),
            person_found=True,
            summary="Berlin-based curator of IDM playlists",
            evidence=(
                Evidence(
                    "https://www.instagram.com/glitchlists/", "glitchlists (@glitchlists) Playlist curator"
                ),
            ),
            spend_usd=Decimal("0.03"),
        )
        agent = FakeAgent(report)

        result = research_lead(a_lead("Our favourite IDM"), pages=FakePages(), agent=agent, budget=Budget())

        assert agent.leads[0].playlist_name == "Glitch Garden"
        assert [(c.route_type, c.value, c.confidence) for c in result.contacts] == [
            ("instagram", "glitchlists", "B")
        ]
        assert result.used_agent
        assert result.spend_usd == Decimal("0.03")
        assert result.summary == "Berlin-based curator of IDM playlists"

    def test_without_an_agent_nothing_found_means_try_again_later(self):
        result = research_lead(a_lead("Our favourite IDM"), pages=FakePages(), agent=None, budget=Budget())

        assert result.contacts == ()
        assert result.deferred


class TestBudget:
    def test_runs_out_of_fetches(self):
        budget = Budget(max_fetches=2)
        budget.spend_fetch()
        budget.spend_fetch()

        assert not budget.can_fetch()

    def test_runs_out_of_time(self):
        ticks = iter([100.0, 100.0, 161.0])
        budget = Budget(max_seconds=60, clock=lambda: next(ticks))

        assert budget.can_fetch()
        assert not budget.can_fetch()


SOURCE = "https://glitchlists.net/about"


class TestVerifyingClaims:
    def test_a_contact_on_the_page_it_cites_keeps_grade_a(self):
        claims = [ClaimedContact("email", "Demos@Glitchlists.net", "A", SOURCE)]
        evidence = [Evidence(SOURCE, "Send demos to demos@glitchlists.net please")]

        assert [(f.value, f.confidence) for f in verify_claims(claims, evidence)] == [
            ("demos@glitchlists.net", "A")
        ]

    def test_grade_a_needs_the_contact_on_its_own_source_page(self):
        claims = [ClaimedContact("email", "demos@glitchlists.net", "A", SOURCE)]
        evidence = [
            Evidence(SOURCE, "About us"),
            Evidence("https://search.example", "demos@glitchlists.net · Glitch"),
        ]

        assert verify_claims(claims, evidence)[0].confidence == "B"

    def test_a_contact_nothing_showed_is_only_a_guess(self):
        claims = [ClaimedContact("instagram", "glitchlists", "B", "https://www.instagram.com/glitchlists/")]

        assert verify_claims(claims, [Evidence(SOURCE, "nothing relevant here")])[0].confidence == "C"

    def test_handles_match_however_they_are_written(self):
        claims = [ClaimedContact("instagram", "@GlitchLists", "A", "https://linktr.ee/glitchlists")]
        evidence = [Evidence("https://linktr.ee/glitchlists/", "https://www.instagram.com/glitchlists/")]

        finding = verify_claims(claims, evidence)[0]

        assert (finding.value, finding.confidence) == ("glitchlists", "A")

    def test_a_short_handle_is_not_found_inside_ordinary_words(self):
        claims = [ClaimedContact("instagram", "dj", "B", SOURCE)]

        assert verify_claims(claims, [Evidence(SOURCE, "late night dj sets")])[0].confidence == "C"

    def test_unusable_claims_are_dropped(self):
        claims = [
            ClaimedContact("email", "not-an-email", "A", SOURCE),
            ClaimedContact("carrier-pigeon", "coo", "A", SOURCE),
        ]

        assert verify_claims(claims, [Evidence(SOURCE, "not-an-email coo")]) == []


def finding(
    route_type="email", value="demos@glitchlists.net", confidence="A", source="https://glitchlists.net"
):
    return ContactFinding(route_type, value, confidence, source, "found it")


def outcome(
    *contacts: ContactFinding, service_account: bool = False, used_agent: bool = True
) -> ResearchResult:
    return ResearchResult(contacts=tuple(contacts), service_account=service_account, used_agent=used_agent)


@pytest.fixture
def curator(session):
    return make_curator(session, display_name="glitchlists", spotify_user_id="glitchlists")


def ready_playlist(session, curator, **overrides):
    return make_playlist(session, curator=curator, status=PlaylistStatus.QUALIFIED, **overrides)


class TestRecording:
    def test_a_strong_contact_is_saved_and_the_playlist_stays_ready(self, session, curator):
        playlist = ready_playlist(session, curator)

        assert record_research(session, playlist, outcome(finding()), today=TODAY, now=NOW)

        contact = session.scalars(select(Contact)).one()
        assert contact.curator_id == curator.id
        assert contact.playlist_id == playlist.spotify_id
        assert contact.contact_key == "email:demos@glitchlists.net"
        assert contact.domain_key == "domain:glitchlists.net"
        assert (contact.confidence, contact.source_url) == ("A", "https://glitchlists.net")
        assert playlist.status == PlaylistStatus.QUALIFIED

    def test_only_a_guess_means_no_contact(self, session, curator):
        playlist = ready_playlist(session, curator)

        reachable = record_research(
            session, playlist, outcome(finding("instagram", "glitchlists", "C")), today=TODAY, now=NOW
        )

        assert not reachable
        assert playlist.status == PlaylistStatus.NO_CONTACT
        assert playlist.last_checked_at == NOW
        assert session.scalars(select(Contact)).one().confidence == "C"

    def test_a_service_account_is_no_contact_even_with_a_form(self, session, curator):
        playlist = ready_playlist(session, curator)
        form = finding("submission-form", "https://volt.fm/contact")

        assert not record_research(
            session, playlist, outcome(form, service_account=True), today=TODAY, now=NOW
        )
        assert playlist.status == PlaylistStatus.NO_CONTACT

    def test_contacts_of_an_excluded_curator_are_not_reused(self, session, curator):
        blocked = make_curator(session, excluded_at=NOW, exclusion_reason="bad-fit")
        make_contact(session, blocked, value="demos@glitchlists.net")
        playlist = ready_playlist(session, curator)

        assert not record_research(session, playlist, outcome(finding()), today=TODAY, now=NOW)
        assert session.scalars(select(Contact).where(Contact.curator_id == curator.id)).all() == []

    def test_a_contact_known_for_another_curator_is_not_duplicated(self, session, curator):
        make_contact(session, make_curator(session), value="demos@glitchlists.net")
        playlist = ready_playlist(session, curator)

        record_research(session, playlist, outcome(finding()), today=TODAY, now=NOW)

        assert len(session.scalars(select(Contact)).all()) == 1

    def test_research_that_could_not_finish_leaves_the_playlist_for_next_time(self, session, curator):
        checked = NOW - timedelta(days=1)
        playlist = ready_playlist(session, curator, last_checked_at=checked)

        assert not record_research(session, playlist, outcome(used_agent=False), today=TODAY, now=NOW)
        assert playlist.status == PlaylistStatus.QUALIFIED
        assert playlist.last_checked_at == checked


def active_profile(session, name: str = "Synman", digest_target: int = 20):
    profile = make_profile(session, name)
    profile.is_active = True
    profile.digest_target = digest_target
    session.flush()
    return profile


def qualified_lead(session, profile, *, fit: float, description: str = "", curator=None):
    playlist = make_playlist(
        session,
        curator=curator or make_curator(session),
        status=PlaylistStatus.QUALIFIED,
        description=description,
    )
    session.add(
        PlaylistProfileFit(
            playlist_id=playlist.spotify_id,
            profile_id=profile.id,
            fit_score=fit,
            reference_artists_present=["Autechre"],
            qualified=True,
        )
    )
    session.flush()
    return playlist


def research_tonight(session, *, agent=None, pages=None, cap: Decimal = Decimal("2")):
    run = start_run(session)
    summary = run_research(
        session,
        pages=pages or FakePages(),
        agent=agent,
        run=run,
        today=TODAY,
        now=NOW,
        spend_cap_usd=cap,
        new_budget=Budget,
    )
    return run, summary


class TestNightlyResearch:
    def test_best_fit_first_and_one_research_per_curator(self, session):
        profile = active_profile(session)
        curator = make_curator(session)
        qualified_lead(session, profile, fit=0.2, curator=curator)
        best = qualified_lead(session, profile, fit=0.9, curator=curator)
        other = qualified_lead(session, profile, fit=0.5)
        agent = FakeAgent()

        research_tonight(session, agent=agent)

        assert [lead.playlist_id for lead in agent.leads] == [best.spotify_id, other.spotify_id]

    def test_stops_once_a_profile_has_enough_reachable_curators(self, session):
        profile = active_profile(session, digest_target=1)
        qualified_lead(session, profile, fit=0.9, description="demos@first.net")
        qualified_lead(session, profile, fit=0.5)
        agent = FakeAgent()

        _, summary = research_tonight(session, agent=agent)

        assert agent.leads == []
        assert summary.reachable == 1

    def test_a_curator_with_a_known_contact_counts_without_research(self, session):
        profile = active_profile(session, digest_target=1)
        known = make_curator(session)
        make_contact(session, known)
        qualified_lead(session, profile, fit=0.9, curator=known)
        qualified_lead(session, profile, fit=0.5)
        agent = FakeAgent()

        research_tonight(session, agent=agent)

        assert agent.leads == []

    def test_curators_on_cooldown_or_excluded_are_skipped(self, session):
        profile = active_profile(session)
        recent = make_curator(session)
        make_outreach(session, recent, profile, TODAY - timedelta(days=10))
        qualified_lead(session, profile, fit=0.9, curator=recent)
        qualified_lead(
            session, profile, fit=0.8, curator=make_curator(session, excluded_at=NOW, exclusion_reason="dead")
        )
        agent = FakeAgent()

        research_tonight(session, agent=agent)

        assert agent.leads == []

    def test_playlists_for_paused_profiles_wait(self, session):
        paused = make_profile(session, "Paused")
        qualified_lead(session, paused, fit=0.9)
        agent = FakeAgent()

        research_tonight(session, agent=agent)

        assert agent.leads == []

    def test_the_budget_stops_claude_but_not_the_free_steps(self, session):
        profile = active_profile(session)
        qualified_lead(session, profile, fit=0.9)
        waiting = qualified_lead(session, profile, fit=0.8)
        free = qualified_lead(session, profile, fit=0.7, description="demos@third.net")
        agent = FakeAgent(AgentReport(spend_usd=Decimal("0.60")))

        run, summary = research_tonight(session, agent=agent, cap=Decimal("0.50"))

        assert len(agent.leads) == 1
        assert waiting.status == PlaylistStatus.QUALIFIED
        assert summary.deferred == 1
        assert summary.budget_reached
        assert session.scalars(select(Contact).where(Contact.playlist_id == free.spotify_id)).one()
        assert run.llm_spend_usd == Decimal("0.60")

    def test_an_agent_failure_leaves_the_lead_for_tomorrow_and_carries_on(self, session):
        profile = active_profile(session)
        first = qualified_lead(session, profile, fit=0.9)
        qualified_lead(session, profile, fit=0.8, description="demos@second.net")

        _, summary = research_tonight(session, agent=FakeAgent(error=RuntimeError("API overloaded")))

        assert first.status == PlaylistStatus.QUALIFIED
        assert summary.reachable == 1
        assert any("API overloaded" in error for error in summary.errors)

    def test_stage_counts_are_recorded(self, session):
        profile = active_profile(session)
        qualified_lead(session, profile, fit=0.9, description="demos@one.net")
        qualified_lead(session, profile, fit=0.8)

        run, _ = research_tonight(session, agent=FakeAgent())

        row = session.scalars(
            select(RunStageCount).where(RunStageCount.run_id == run.id, RunStageCount.stage == "research")
        ).one()
        assert (row.count_in, row.count_out) == (2, 1)


class TestConversationAllowance:
    def test_a_full_profile_is_not_researched(self, session):
        """A profile with no room tonight shouldn't pay five cents a lead for leads it can't use."""
        profile = active_profile(session, digest_target=5)
        profile.open_conversation_limit = 2
        for _ in range(2):
            open_conversation(session, profile, now=NOW)
        qualified_lead(session, profile, fit=0.9, description="demos@first.net")
        agent = FakeAgent()

        _, summary = research_tonight(session, agent=agent)

        assert agent.leads == []
        assert summary.researched == 0

    def test_research_stops_at_the_allowance_not_the_target(self, session):
        """Target 10, ceiling 12, 8 open: research readies 4, not 10."""
        profile = active_profile(session, digest_target=10)
        profile.open_conversation_limit = 12
        for _ in range(8):
            open_conversation(session, profile, now=NOW)
        for number in range(6):
            qualified_lead(session, profile, fit=0.9, description=f"demos{number}@first.net")

        _, summary = research_tonight(session, agent=FakeAgent())

        assert summary.reachable == 4
