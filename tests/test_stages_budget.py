"""Research spends what's left of the night's Claude budget after evaluation's fit checks."""

from decimal import Decimal

import pipeline.stages as stages
from core.app_settings import set_nightly_claude_budget
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
