"""The Claude stages of a run, contact research and the digest, as the run report shows them."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pipeline.stages as stages
from core.app_settings import set_nightly_claude_budget
from pipeline.digest import DigestSummary
from pipeline.research import ResearchSummary
from pipeline.runs import start_run

TODAY = date(2026, 9, 15)
NOW = datetime(2026, 9, 15, 2, 5, tzinfo=UTC)


def test_research_spends_up_to_the_budget_set_in_the_web_app(session, monkeypatch):
    seen: dict = {}

    def fake_run_research(session, **kwargs):
        seen.update(kwargs)
        return ResearchSummary(researched=3, reachable=2, no_contact=1, spend_usd=Decimal("0.12"))

    monkeypatch.setattr(stages, "run_research", fake_run_research)
    set_nightly_claude_budget(session, "3.50")
    run = start_run(session)

    report = stages.research_stage(pages="pages", agent="agent")(session, run=run, today=TODAY, now=NOW)

    assert seen["spend_cap_usd"] == Decimal("3.50")
    assert (seen["pages"], seen["agent"], seen["run"], seen["today"], seen["now"]) == (
        "pages",
        "agent",
        run,
        TODAY,
        NOW,
    )
    text = "\n".join(report.lines)
    assert report.name == "Contact research"
    assert "3 researched" in text
    assert "2 reachable" in text
    assert "$0.12 of $3.50" in text


def test_research_says_when_the_budget_ran_out_and_what_went_wrong(session, monkeypatch):
    summary = ResearchSummary(deferred=4, budget_reached=True, errors=["123: RuntimeError: boom"])
    monkeypatch.setattr(stages, "run_research", lambda session, **kwargs: summary)

    report = stages.research_stage(pages=None, agent=None)(
        session, run=start_run(session), today=TODAY, now=NOW
    )

    text = "\n".join(report.lines)
    assert "budget" in text.lower()
    assert "4 left for another night" in text
    assert "boom" in text


def test_the_digest_reports_entries_per_profile(session, monkeypatch):
    seen: dict = {}

    def fake_build_digest(session, **kwargs):
        seen.update(kwargs)
        return DigestSummary(
            entries=12, template_briefs=1, per_profile={"Synman": 12}, spend_usd=Decimal("0.02")
        )

    monkeypatch.setattr(stages, "build_digest", fake_build_digest)

    report = stages.digest_stage(writer="writer")(session, run=start_run(session), today=TODAY, now=NOW)

    text = "\n".join(report.lines)
    assert seen["writer"] == "writer"
    assert report.name == "Digest"
    assert "12 entries" in text
    assert "Synman: 12" in text
    assert "1 plain brief" in text
    assert "fewer than" not in text


def test_the_digest_says_when_the_budget_made_the_briefs_plain(session, monkeypatch):
    summary = DigestSummary(entries=12, budget_briefs=3, per_profile={"Synman": 12})
    monkeypatch.setattr(stages, "build_digest", lambda session, **kwargs: summary)

    report = stages.digest_stage(writer="writer")(session, run=start_run(session), today=TODAY, now=NOW)

    text = "\n".join(report.lines)
    assert "3 plain briefs" in text
    assert "budget" in text.lower()
    assert "couldn't write" not in text


def test_a_brief_claude_fumbled_is_not_blamed_on_the_budget(session, monkeypatch):
    summary = DigestSummary(entries=12, template_briefs=2, per_profile={"Synman": 12})
    monkeypatch.setattr(stages, "build_digest", lambda session, **kwargs: summary)

    report = stages.digest_stage(writer="writer")(session, run=start_run(session), today=TODAY, now=NOW)

    text = "\n".join(report.lines)
    assert "2 plain briefs, because Claude couldn't write them" in text
    assert "budget" not in text.lower()


def test_a_small_digest_says_so_plainly(session, monkeypatch):
    summary = DigestSummary(entries=4, per_profile={"Synman": 4})
    monkeypatch.setattr(stages, "build_digest", lambda session, **kwargs: summary)

    report = stages.digest_stage(writer=None)(session, run=start_run(session), today=TODAY, now=NOW)

    assert "fewer than 10" in "\n".join(report.lines)
