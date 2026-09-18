"""The Claude stages of a run: contact research, then the digest.

`run_pipeline` knows nothing about Claude. It calls each stage with the session, the run and
the night's dates, and prints the lines the stage reports. Each stage reads the nightly budget
from the web app's settings once, as it starts, so the limit Jarred sets that evening is the
one that applies. Research gets what's left after evaluation's fit checks, and the digest gets
what's left after research. Reading it once rather than per lead or per entry keeps both loops
cheap and easy to reason about.

The run report says plainly what happened: how many curators were researched and reached,
what Claude cost against the budget, what was left for another night, whether briefs came out
plain and why, and whether a profile got fewer than 10 digest entries.
"""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from core.app_settings import nightly_claude_budget
from core.models import Run
from pipeline.digest import BriefWriter, DigestSummary, build_digest
from pipeline.nightly import StageReport
from pipeline.research import PageSource, ResearchAgent, ResearchSummary, run_research

SHORT_DIGEST = 10


def research_stage(*, pages: PageSource, agent: ResearchAgent | None) -> Callable[..., StageReport]:
    def stage(session: Session, *, run: Run, today: date, now: datetime) -> StageReport:
        spent_so_far = run.llm_spend_usd or Decimal(0)
        left = max(Decimal(0), nightly_claude_budget(session) - spent_so_far)
        summary = run_research(
            session, pages=pages, agent=agent, run=run, today=today, now=now, spend_cap_usd=left
        )
        return StageReport("Contact research", tuple(_research_lines(summary, left)))

    return stage


def digest_stage(*, writer: BriefWriter | None) -> Callable[..., StageReport]:
    def stage(session: Session, *, run: Run, today: date, now: datetime) -> StageReport:
        spent_so_far = run.llm_spend_usd or Decimal(0)
        left = max(Decimal(0), nightly_claude_budget(session) - spent_so_far)
        summary = build_digest(session, writer=writer, run=run, today=today, now=now, spend_cap_usd=left)
        return StageReport("Digest", tuple(_digest_lines(summary)))

    return stage


def _research_lines(summary: ResearchSummary, budget_left: Decimal) -> list[str]:
    lines = [
        f"{summary.researched} researched · {summary.reachable} reachable · "
        f"{summary.no_contact} no contact · {summary.already_reachable} already reachable",
        f"Claude spend ${summary.spend_usd:.2f} of ${budget_left:.2f} left",
    ]
    if summary.deferred:
        lines.append(f"{summary.deferred} left for another night")
    if summary.budget_reached:
        lines.append("The nightly Claude budget was reached. Raise it on the Profiles page to research more.")
    lines.extend(f"error: {error}" for error in summary.errors)
    return lines


def _digest_lines(summary: DigestSummary) -> list[str]:
    lines = [f"{summary.entries} {'entry' if summary.entries == 1 else 'entries'} tonight"]
    if not summary.per_profile:
        lines.append(f"Nothing reached the digest tonight (fewer than {SHORT_DIGEST}).")
    for name, entries in sorted(summary.per_profile.items()):
        short = f" (fewer than {SHORT_DIGEST}, so tonight's list is short)" if entries < SHORT_DIGEST else ""
        lines.append(f"{name}: {entries}{short}")
    if summary.template_briefs:
        plural = "brief" if summary.template_briefs == 1 else "briefs"
        lines.append(f"{summary.template_briefs} plain {plural}, because Claude couldn't write them")
    if summary.budget_briefs:
        plural = "brief" if summary.budget_briefs == 1 else "briefs"
        lines.append(
            f"{summary.budget_briefs} plain {plural}, because the nightly Claude budget was reached. "
            "Every lead is still here; raise it on the Profiles page for written briefs."
        )
    lines.append(f"Claude spend ${summary.spend_usd:.2f}")
    lines.extend(f"error: {error}" for error in summary.errors)
    return lines
