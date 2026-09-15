"""Run bookkeeping: each run records when it ran, how it ended and what each stage did."""

from datetime import UTC, datetime

from sqlalchemy import select

from core.models import RunStageCount
from core.profiles import create_profile
from pipeline.runs import finish_run, record_stage, start_run
from tests.factories import default_artist_id

NOW = datetime(2026, 9, 14, 2, 30, tzinfo=UTC)


def test_starting_a_run_records_its_trigger(session):
    run = start_run(session, trigger="manual")

    assert run.id is not None
    assert (run.trigger, run.status, run.finished_at) == ("manual", "running", None)


def test_finishing_a_successful_run(session):
    run = start_run(session, trigger="schedule")

    finish_run(session, run, now=NOW)

    assert (run.status, run.finished_at, run.error) == ("succeeded", NOW, None)


def test_finishing_a_failed_run_keeps_the_reason(session):
    run = start_run(session, trigger="schedule")

    finish_run(session, run, now=NOW, error="Serper: invalid API key (HTTP 401)")

    assert run.status == "failed"
    assert run.error == "Serper: invalid API key (HTTP 401)"


def test_stage_counts_are_recorded_per_profile(session):
    run = start_run(session, trigger="manual")
    profile = create_profile(session, default_artist_id(session), "Synman")

    record_stage(session, run, "discover", count_in=59, count_out=41, profile_id=profile.id)

    row = session.scalar(select(RunStageCount).where(RunStageCount.run_id == run.id))
    assert (row.stage, row.profile_id, row.count_in, row.count_out) == ("discover", profile.id, 59, 41)


def test_recording_the_same_stage_again_adds_to_it(session):
    run = start_run(session, trigger="manual")

    record_stage(session, run, "discover", count_in=10, count_out=4)
    record_stage(session, run, "discover", count_in=5, count_out=1)

    rows = session.scalars(select(RunStageCount).where(RunStageCount.run_id == run.id)).all()
    assert len(rows) == 1
    assert (rows[0].count_in, rows[0].count_out) == (15, 5)
