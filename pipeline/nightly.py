"""One pipeline run: discover for the active profiles, fetch and judge the candidates, then the later stages.

The run record is created first and committed straight away, so even a run that crashes in
its first minute leaves a trace. Each profile's discovery is committed as it finishes, and
evaluation commits per playlist. If anything unexpected goes wrong, the run is marked failed
with the error, and the error is raised again so nothing hides it. Being blocked by Spotify
isn't a crash, but it still marks the run failed: the web app's status panel should say so.

The caller can hand in a fit judge, which evaluation uses to give no-fit playlists a Claude
check, and the later stages (contact research, then the digest). The stages run after
evaluation, in order, each committing its own work. They run even when Spotify pushed back,
because they don't need Spotify. A failing stage fails the run, but the night's earlier work
stays.

Asking for a specific profile that isn't active is refused before a run is even recorded.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import Profile
from core.profiles import get_profile
from pipeline.discover import ArtistGraph, DiscoveredOnOutcome, DiscoverySummary, discover_for_profile
from pipeline.evaluate import EvaluationSummary, PlaylistFetcher, evaluate_candidates
from pipeline.fit_judge import FitJudge
from pipeline.runs import finish_run, start_run
from pipeline.search import SearchProvider

BLOCKED = "Spotify pushed back (blocked); the run stopped early and the rest stays queued."


@dataclass(frozen=True)
class StageReport:
    name: str
    lines: tuple[str, ...]


# A later stage is called as stage(session, run=run, today=today, now=now) and says what it did.
Stage = Callable[..., StageReport]


@dataclass(frozen=True)
class RunReport:
    run_id: int
    discoveries: tuple[DiscoverySummary, ...]
    evaluation: EvaluationSummary
    profile_names: dict[int, str]
    stages: tuple[StageReport, ...] = ()


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
    stages: Sequence[Stage] = (),
    fit_judge: FitJudge | None = None,
    artist_graph: ArtistGraph | None = None,
) -> RunReport:
    profiles = _profiles_to_discover(session, profile_id)
    names = {profile.id: profile.name for profile in profiles}
    run = start_run(session, trigger)
    session.commit()

    try:
        discoveries = []
        for profile_id_to_search in names:
            discoveries.append(
                discover_for_profile(
                    session,
                    profile_id_to_search,
                    providers,
                    run=run,
                    today=today,
                    artist_graph=artist_graph,
                )
            )
            session.commit()
        evaluation = evaluate_candidates(
            session, fetcher, run=run, today=today, now=now, limit=fetch_limit, fit_judge=fit_judge
        )
        stage_reports = []
        for stage in stages:
            stage_reports.append(stage(session, run=run, today=today, now=now))
            session.commit()
        # `now` is when the run started (it anchors the liveness checks); the finish time is real time.
        finish_run(session, run, error=BLOCKED if evaluation.blocked else None)
        session.commit()
    except Exception as error:
        session.rollback()
        finish_run(session, run, error=f"{type(error).__name__}: {error}")
        session.commit()
        raise

    return RunReport(
        run_id=run.id,
        discoveries=tuple(discoveries),
        evaluation=evaluation,
        profile_names=names,
        stages=tuple(stage_reports),
    )


def describe_run(report: RunReport) -> str:
    lines = [f"Run {report.run_id}"]
    for discovery in report.discoveries:
        lines.append(f"\n{report.profile_names[discovery.profile_id]}")
        lines.extend(_discovered_on_lines(discovery.discovered_on))
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
    if evaluation.claude_checks:
        lines.append(
            f"Claude fit checks: {evaluation.claude_checks}, {evaluation.claude_fits} fitted "
            f"(${evaluation.spend_usd:.2f})"
        )
    if evaluation.blocked:
        lines.append(BLOCKED)
    lines.extend(f"  {error}" for error in evaluation.errors)

    for stage in report.stages:
        lines.append(f"\n{stage.name}")
        lines.extend(f"  {line}" for line in stage.lines)
    return "\n".join(lines)


def _discovered_on_lines(found: DiscoveredOnOutcome | None) -> list[str]:
    """The artist graph's contribution, in the same voice as the per-term lines.

    Nothing at all when the source didn't run, so a report from a caller that passes no client
    reads exactly as it did before this existed.
    """
    if found is None:
        return []
    lines = [f"  Discovered on: {found.artists} artists, {found.playlists} playlists, {found.new} new"]
    if found.unresolved:
        # Naming them matters: each is one edit in the profile editor, and a silent zero would be
        # indistinguishable from the whole source not running.
        lines.append(f"    no Spotify id yet: {', '.join(found.unresolved)}")
    lines.extend(f"    error: {error}" for error in found.errors)
    return lines


def _profiles_to_discover(session: Session, profile_id: int | None) -> list[Profile]:
    if profile_id is not None:
        profile = get_profile(session, profile_id)
        if not profile.is_active:
            raise ValueError(f"Profile “{profile.name}” is not active; activate it before running.")
        return [profile]
    return list(
        session.scalars(select(Profile).where(Profile.is_active.is_(True)).order_by(func.lower(Profile.name)))
    )
