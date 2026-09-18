"""Each Claude stage spends only what's left of the night's budget when it starts.

Research gets what evaluation's fit checks left; the digest gets what research left after that.
"""

from decimal import Decimal

import pipeline.stages as stages
from core.app_settings import set_nightly_claude_budget
from pipeline.digest import DigestSummary
from pipeline.research import ResearchSummary
from pipeline.runs import start_run
from tests.test_pipeline_stages import NOW, TODAY


def research_with_spent_so_far(session, monkeypatch, spent: str):
    seen: dict = {}

    def fake_run_research(session, **kwargs):
        seen.update(kwargs)
        return ResearchSummary()

    monkeypatch.setattr(stages, "run_research", fake_run_research)
    set_nightly_claude_budget(session, "2.00")
    run = start_run(session)
    run.llm_spend_usd = Decimal(spent)
    session.flush()
    report = stages.research_stage(pages=None, agent=None)(session, run=run, today=TODAY, now=NOW)
    return seen, report


def test_research_gets_what_is_left_after_the_fit_checks(session, monkeypatch):
    seen, report = research_with_spent_so_far(session, monkeypatch, "0.30")

    assert seen["spend_cap_usd"] == Decimal("1.70")
    assert "of $1.70 left" in "\n".join(report.lines)


def test_an_overspent_night_leaves_nothing_rather_than_a_negative_budget(session, monkeypatch):
    seen, _ = research_with_spent_so_far(session, monkeypatch, "2.40")

    assert seen["spend_cap_usd"] == Decimal("0")


def digest_with_spent_so_far(session, monkeypatch, spent: str):
    seen: dict = {}

    def fake_build_digest(session, **kwargs):
        seen.update(kwargs)
        return DigestSummary()

    monkeypatch.setattr(stages, "build_digest", fake_build_digest)
    set_nightly_claude_budget(session, "2.00")
    run = start_run(session)
    run.llm_spend_usd = Decimal(spent)
    session.flush()
    stages.digest_stage(writer=None)(session, run=run, today=TODAY, now=NOW)
    return seen


def test_the_digest_gets_what_research_left(session, monkeypatch):
    seen = digest_with_spent_so_far(session, monkeypatch, "1.40")

    assert seen["spend_cap_usd"] == Decimal("0.60")


def test_a_night_already_at_the_cap_leaves_the_digest_nothing(session, monkeypatch):
    seen = digest_with_spent_so_far(session, monkeypatch, "2.05")

    assert seen["spend_cap_usd"] == Decimal("0")
