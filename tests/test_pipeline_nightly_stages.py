"""run_pipeline with its later stages: contact research and the digest run after evaluation.

The stages here are simple recorders, so these tests are about order, what each stage can see,
and what a failing stage does to the run, not about research or the digest themselves.
"""

import pytest
from sqlalchemy import select

from core.models import Playlist, Run
from core.profiles import create_profile, set_profile_active
from pipeline.nightly import StageReport, describe_run, run_pipeline
from tests.profile_helpers import add_contents
from tests.test_pipeline_nightly import NOW, TODAY, FakeFetcher, FakeProvider, pid


@pytest.fixture
def active_profile(session):
    profile = add_contents(session, create_profile(session, "Synman"))
    set_profile_active(session, profile.id, True)
    return profile


class RecordingStage:
    def __init__(self, name: str, log: list[str], error: Exception | None = None):
        self.name = name
        self.log = log
        self.error = error
        self.calls: list[tuple] = []

    def __call__(self, session, *, run, today, now) -> StageReport:
        self.log.append(self.name)
        self.calls.append((run.id, today, now))
        if self.error:
            raise self.error
        return StageReport(self.name, (f"{self.name} done",))


def run_with(session, stages):
    return run_pipeline(
        session,
        providers=[FakeProvider({"term 0": [pid(1)]})],
        fetcher=FakeFetcher(),
        trigger="manual",
        today=TODAY,
        now=NOW,
        stages=stages,
    )


def test_stages_run_after_evaluation_in_the_order_given(session, active_profile):
    log: list[str] = []
    research = RecordingStage("Contact research", log)
    digest = RecordingStage("Digest", log)

    report = run_with(session, [research, digest])

    assert log == ["Contact research", "Digest"]
    assert research.calls == [(report.run_id, TODAY, NOW)]
    assert [stage.name for stage in report.stages] == ["Contact research", "Digest"]
    assert session.get(Run, report.run_id).status == "succeeded"
    text = describe_run(report)
    assert "Contact research" in text
    assert "Digest done" in text


def test_a_stage_sees_the_nights_evaluated_playlists(session, active_profile):
    seen: list[str] = []

    def peek(session, *, run, today, now) -> StageReport:
        seen.append(session.get(Playlist, pid(1)).status)
        return StageReport("Peek", ())

    run_with(session, [peek])

    assert seen == ["qualified"]


def test_a_failing_stage_fails_the_run_but_keeps_the_earlier_work(session, active_profile):
    broken = RecordingStage("Contact research", [], error=RuntimeError("research broke"))

    with pytest.raises(RuntimeError, match="research broke"):
        run_with(session, [broken])

    failed = session.scalar(select(Run).order_by(Run.id.desc()))
    assert failed.status == "failed"
    assert "research broke" in failed.error
    assert session.get(Playlist, pid(1)).status == "qualified"


def test_without_stages_a_run_is_just_discovery_and_evaluation(session, active_profile):
    report = run_with(session, [])

    assert report.stages == ()
