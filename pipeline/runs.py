"""Run bookkeeping.

Every pipeline run leaves a record: how it was started, how it ended (and why, if it failed),
and what each stage did per profile. The digest footer and the web app's status panel read
these rows, so drift in the pipeline is visible without digging through logs.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Run, RunStageCount, RunStatus, RunTrigger


def start_run(session: Session, trigger: str = RunTrigger.MANUAL) -> Run:
    run = Run(trigger=trigger, status=RunStatus.RUNNING)
    session.add(run)
    session.flush()
    return run


def finish_run(session: Session, run: Run, *, now: datetime | None = None, error: str | None = None) -> Run:
    run.finished_at = now or datetime.now(UTC)
    run.status = RunStatus.FAILED if error else RunStatus.SUCCEEDED
    run.error = error
    session.flush()
    return run


def record_stage(
    session: Session, run: Run, stage: str, *, count_in: int, count_out: int, profile_id: int | None = None
) -> RunStageCount:
    """Add to this run's counts for a stage (and profile), creating the row the first time."""
    same_profile = (
        RunStageCount.profile_id.is_(None) if profile_id is None else RunStageCount.profile_id == profile_id
    )
    row = session.scalar(
        select(RunStageCount).where(
            RunStageCount.run_id == run.id, RunStageCount.stage == stage, same_profile
        )
    )
    if row is None:
        row = RunStageCount(run_id=run.id, profile_id=profile_id, stage=stage, count_in=0, count_out=0)
        session.add(row)
    row.count_in += count_in
    row.count_out += count_out
    session.flush()
    return row
