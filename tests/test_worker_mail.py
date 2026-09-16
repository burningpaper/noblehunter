"""The runner's mail thread: reading replies on a timer, without disturbing the nightly run."""

import threading
import time

from pipeline.worker import keep_reading_mail

SETTLE_SECONDS = 0.1


class CountingSync:
    """Stands in for a round of mail syncing, and can be told to fail."""

    def __init__(self, error: Exception | None = None):
        self.calls = 0
        self.error = error
        self.called = threading.Event()

    def __call__(self) -> None:
        self.calls += 1
        self.called.set()
        if self.error:
            raise self.error


def test_it_reads_the_mail_while_the_work_goes_on():
    sync = CountingSync()

    with keep_reading_mail(sync, every=0.01):
        assert sync.called.wait(timeout=2), "the mail thread never ran"

    assert sync.calls >= 1


def test_it_stops_when_the_work_is_done():
    sync = CountingSync()

    with keep_reading_mail(sync, every=0.01):
        sync.called.wait(timeout=2)
    after = sync.calls

    time.sleep(SETTLE_SECONDS)
    assert sync.calls == after


def test_stopping_doesnt_wait_out_a_round_in_flight():
    # A round talking to a slow Gmail must not keep the runner up: the join waits the grace
    # period, not the five-minute interval. The thread is a daemon, so the process still leaves.
    started, release = threading.Event(), threading.Event()

    def slow_sync() -> None:
        started.set()
        release.wait(timeout=5)

    began = time.monotonic()
    with keep_reading_mail(slow_sync, every=0.01, shutdown_grace=0.05):
        assert started.wait(timeout=2), "the mail thread never ran"
    elapsed = time.monotonic() - began
    release.set()

    assert elapsed < 1


def test_a_failing_sync_doesnt_stop_the_thread():
    sync = CountingSync(error=RuntimeError("Gmail is down"))

    with keep_reading_mail(sync, every=0.01):
        assert sync.called.wait(timeout=2)
        deadline = time.monotonic() + 2
        while sync.calls < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

    assert sync.calls >= 2  # it kept going after the failure
