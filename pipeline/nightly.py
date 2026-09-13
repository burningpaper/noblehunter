"""One pipeline run: discover for the active profiles, then fetch and judge the candidates.

The run record is created first and committed straight away, so even a run that crashes in
its first minute leaves a trace. Each profile's discovery is committed as it finishes, and
evaluation commits per playlist. If anything unexpected goes wrong, the run is marked failed
with the error, and the error is raised again so nothing hides it. Being blocked by Spotify
isn't a crash, but it still marks the run failed: the web app's status panel should say so.

Asking for a specific profile that isn't active is refused before a run is even recorded.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import Profile
from core.profiles import get_profile
from pipeline.discover import DiscoverySummary, discover_for_profile
from pipeline.evaluate import EvaluationSummary, PlaylistFetcher, evaluate_candidates
from pipeline.runs import finish_run, start_run
from pipeline.search import SearchProvider

BLOCKED = "Spotify pushed back (blocked); the run stopped early and the rest stays queued."


@dataclass(frozen=True)
class RunReport:
    run_id: int
    discoveries: tuple[DiscoverySummary, ...]
    evaluation: EvaluationSummary
    profile_names: dict[int, str]


def run_pipeline(
    session: Session,
    *,
    providers: Sequence[SearchProvider],
    fetcher: PlaylistFetcher,
    trigger: str,
    today: date,
    now: datetime,
    profile_id: int | None = None,
    fetch_limit: int | None = None,
) -> RunReport:
    profiles = _profiles_to_discover(session, profile_id)
    names = {profile.id: profile.name for profile in profiles}
    run = start_run(session, trigger)
    session.commit()

    try:
        discoveries = []
        for profile_id_to_search in names:
            discoveries.append(
                discover_for_profile(session, profile_id_to_search, providers, run=run, today=today)
            )
            session.commit()
        evaluation = evaluate_candidates(session, fetcher, run=run, today=today, now=now, limit=fetch_limit)
        finish_run(session, run, now=now, error=BLOCKED if evaluation.blocked else None)
        session.commit()
    except Exception as error:
        session.rollback()
        finish_run(session, run, now=now, error=f"{type(error).__name__}: {error}")
        session.commit()
        raise

    return RunReport(
        run_id=run.id, discoveries=tuple(discoveries), evaluation=evaluation, profile_names=names
    )


def describe_run(report: RunReport) -> str:
    lines = [f"Run {report.run_id}"]
    for discovery in report.discoveries:
        lines.append(f"\n{report.profile_names[discovery.profile_id]}")
        if not discovery.terms:
            lines.append("  no active search terms")
        for outcome in discovery.terms:
            lines.append(
                f"  {outcome.term}: {outcome.candidates} found, {outcome.new} new, "
                f"{outcome.requeued} requeued, {outcome.skipped} skipped"
            )
            lines.extend(f"    warning: {warning}" for warning in outcome.warnings)
            lines.extend(f"    error: {error}" for error in outcome.errors)

    evaluation = report.evaluation
    lines.append(
        f"\nFetched {evaluation.fetched} · failed {evaluation.failed} · "
        f"qualified {evaluation.qualified} · rejected {evaluation.rejected}"
    )
    if evaluation.blocked:
        lines.append(BLOCKED)
    lines.extend(f"  {error}" for error in evaluation.errors)
    return "\n".join(lines)


def _profiles_to_discover(session: Session, profile_id: int | None) -> list[Profile]:
    if profile_id is not None:
        profile = get_profile(session, profile_id)
        if not profile.is_active:
            raise ValueError(f"Profile “{profile.name}” is not active; activate it before running.")
        return [profile]
    return list(
        session.scalars(select(Profile).where(Profile.is_active.is_(True)).order_by(func.lower(Profile.name)))
    )
