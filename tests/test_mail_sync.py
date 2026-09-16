"""Bringing replies in from Gmail: matching them to entries, and ignoring everything else."""

import base64
from datetime import UTC, date, datetime

from sqlalchemy import func, select

from core.gmail import GmailError
from core.mail_sync import sync_all, sync_mailbox
from core.models import EmailMessage, MailDirection
from tests.factories import (
    make_artist,
    make_curator,
    make_mailbox,
    make_outreach,
    make_playlist,
    make_profile,
)

NIGHT = date(2026, 9, 16)
NOW = datetime(2026, 9, 17, 6, 0, tzinfo=UTC)
MAILBOX_ADDRESS = "synman@gmail.com"
CURATOR_ADDRESS = "nina@broken-machines.com"


def gmail_message(
    message_id: str, thread_id: str, *, sender: str, body: str, sent_ms: int = 1_758_000_000_000
):
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": str(sent_ms),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": MAILBOX_ADDRESS},
                {"name": "Subject", "value": "Re: Kelvin"},
                {"name": "Message-ID", "value": f"<{message_id}@mail.gmail.com>"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


class FakeGmail:
    """Stands in for one mailbox's Gmail: canned history, messages and threads."""

    def __init__(
        self, history=(), messages=None, threads=None, history_id="2000", error=None, message_error=None
    ):
        self.history = list(history)
        self.messages = messages or {}
        self.threads = threads or {}
        self.new_history_id = history_id
        self.error = error
        self.message_error = message_error
        self.history_calls: list[str] = []
        self.fetched: list[str] = []

    def history_since(self, history_id: str):
        self.history_calls.append(history_id)
        if self.error:
            raise self.error
        return list(self.history), self.new_history_id

    def message(self, message_id: str) -> dict:
        self.fetched.append(message_id)
        if self.message_error:
            raise self.message_error
        return self.messages[message_id]

    def thread(self, thread_id: str) -> dict:
        return self.threads[thread_id]

    def profile(self) -> tuple[str, str]:
        return MAILBOX_ADDRESS, self.new_history_id


def a_pitched_entry(session, *, thread_id="thread1"):
    # One artist (and so one mailbox) per entry: artist names are unique, and the tests that
    # build two entries need two mailboxes to sync.
    artist = make_artist(session, f"Synman ({thread_id})")
    profile = make_profile(session, "IDM Playlists", artist=artist)
    mailbox = make_mailbox(session, artist, address=MAILBOX_ADDRESS, history_id="1000")
    profile.mail_account_id = mailbox.id
    curator = make_curator(session, display_name="Nina")
    playlist = make_playlist(session, curator=curator)
    outreach = make_outreach(session, curator, profile, NIGHT, playlist=playlist)
    outreach.mail_account_id = mailbox.id
    outreach.gmail_thread_id = thread_id
    session.flush()
    return outreach, mailbox


def a_reply_gmail(message_id="reply1", thread_id="thread1", sender=CURATOR_ADDRESS, body="Send it over."):
    return FakeGmail(
        history=[(message_id, thread_id)],
        messages={message_id: gmail_message(message_id, thread_id, sender=sender, body=body)},
    )


class TestBringingRepliesIn:
    def test_a_reply_lands_on_the_entry_it_belongs_to(self, session):
        outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail()

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert outcome.stored == 1
        message = session.scalars(select(EmailMessage)).one()
        assert message.outreach_id == outreach.id
        assert message.direction == MailDirection.IN
        assert message.from_address == CURATOR_ADDRESS
        assert message.body_text == "Send it over."
        assert message.rfc822_message_id == "<reply1@mail.gmail.com>"

    def test_our_own_sent_mail_is_recorded_as_outgoing(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail(message_id="mine1", sender=MAILBOX_ADDRESS, body="Hi Nina")

        sync_mailbox(session, mailbox, gmail, now=NOW)

        assert session.scalars(select(EmailMessage)).one().direction == MailDirection.OUT

    def test_mail_on_an_unknown_thread_is_never_stored(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail(message_id="bank1", thread_id="someone-elses-thread")

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert (outcome.stored, outcome.ignored) == (0, 1)
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 0
        assert gmail.fetched == []  # not even read: the thread id alone says it isn't ours

    def test_another_mailboxs_thread_is_not_claimed(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        other = make_mailbox(session, make_artist(session), address="someone@gmail.com", history_id="1000")
        gmail = a_reply_gmail()

        outcome = sync_mailbox(session, other, gmail, now=NOW)

        assert outcome.stored == 0

    def test_syncing_twice_stores_one_copy(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail()
        sync_mailbox(session, mailbox, gmail, now=NOW)

        second = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert second.stored == 0
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 1

    def test_it_moves_the_history_id_on_and_checks_in(self, session):
        _outreach, mailbox = a_pitched_entry(session)

        sync_mailbox(session, mailbox, a_reply_gmail(), now=NOW)

        assert mailbox.history_id == "2000"
        assert mailbox.last_checked_at == NOW

    def test_it_asks_gmail_only_for_what_changed(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = a_reply_gmail()

        sync_mailbox(session, mailbox, gmail, now=NOW)

        assert gmail.history_calls == ["1000"]


class TestWhenGmailSaysNo:
    def test_an_expired_history_id_rereads_the_threads_we_know(self, session):
        outreach, mailbox = a_pitched_entry(session)
        reply = gmail_message("reply1", "thread1", sender=CURATOR_ADDRESS, body="Yes")
        # The thread lists the message; the sync then fetches it by id, as it does for history.
        gmail = FakeGmail(
            error=GmailError("history-gone", "That history id is too old"),
            messages={"reply1": reply},
            threads={"thread1": {"messages": [reply]}},
        )

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert outcome.reset is True
        assert outcome.stored == 1
        assert session.scalars(select(EmailMessage)).one().outreach_id == outreach.id
        assert mailbox.history_id == "2000"  # taken fresh from the profile call

    def test_refused_access_marks_the_mailbox_for_reconnection(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = FakeGmail(error=GmailError("auth", "Google refused the saved Gmail access"))

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert mailbox.needs_reconnect is True
        assert outcome.error
        assert mailbox.history_id == "1000"  # unchanged, so nothing is skipped after reconnecting

    def test_a_setup_problem_is_recorded_without_demanding_a_reconnect(self, session):
        # The Gmail API being switched off for the Google project is not this mailbox's fault,
        # and nothing clears `needs_reconnect` but a person completing Google's consent flow. A
        # flag here would outlast the problem and send someone round that flow for nothing.
        _outreach, mailbox = a_pitched_entry(session)
        gmail = FakeGmail(error=GmailError("config", "The Gmail API isn't enabled for the project"))

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert mailbox.needs_reconnect is False
        assert outcome.error
        assert mailbox.last_error == "The Gmail API isn't enabled for the project"
        assert mailbox.history_id == "1000"  # unchanged, so nothing is skipped once it's enabled

    def test_a_setup_problem_reading_a_message_stops_the_round(self, session):
        # Mid-round it is no different: every later call fails the same way, so carrying on just
        # counts the whole mailbox as "ignored" and reports a clean round.
        _outreach, mailbox = a_pitched_entry(session)
        gmail = FakeGmail(
            history=[("reply1", "thread1")],
            message_error=GmailError("config", "The Gmail API isn't enabled for the project"),
        )

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert outcome.error
        assert (outcome.stored, outcome.ignored) == (0, 0)
        assert mailbox.needs_reconnect is False
        assert mailbox.history_id == "1000"

    def test_a_transient_failure_changes_nothing(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = FakeGmail(error=GmailError("transient", "Gmail is busy"))

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert outcome.error
        assert mailbox.needs_reconnect is False
        assert mailbox.history_id == "1000"

    def test_a_message_that_cannot_be_read_is_skipped_not_fatal(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        gmail = FakeGmail(
            history=[("bad1", "thread1"), ("reply1", "thread1")],
            messages={
                "bad1": {"id": "bad1", "threadId": "thread1"},  # no payload, no date
                "reply1": gmail_message("reply1", "thread1", sender=CURATOR_ADDRESS, body="Yes"),
            },
        )

        outcome = sync_mailbox(session, mailbox, gmail, now=NOW)

        assert (outcome.stored, outcome.ignored) == (1, 1)
        assert session.scalars(select(EmailMessage)).one().gmail_message_id == "reply1"
        assert mailbox.history_id == "2000"


class TestAllMailboxes:
    def test_it_syncs_every_connected_mailbox(self, session):
        _first, mailbox = a_pitched_entry(session)
        _second, other = a_pitched_entry(session, thread_id="thread2")
        opened: list[int] = []

        def open_gmail(box):
            opened.append(box.id)
            return FakeGmail()  # nothing new in either mailbox

        sync_all(session, open_gmail=open_gmail, now=NOW)

        assert sorted(opened) == sorted([mailbox.id, other.id])

    def test_a_disconnected_mailbox_is_left_alone(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        mailbox.refresh_token_encrypted = None
        session.flush()
        opened: list[int] = []

        def open_gmail(box):
            opened.append(box.id)
            return FakeGmail()

        sync_all(session, open_gmail=open_gmail, now=NOW)

        assert opened == []

    def test_a_mailbox_awaiting_reconnection_is_skipped(self, session):
        _outreach, mailbox = a_pitched_entry(session)
        mailbox.needs_reconnect = True
        session.flush()
        opened: list[int] = []

        def open_gmail(box):
            opened.append(box.id)
            return FakeGmail()

        sync_all(session, open_gmail=open_gmail, now=NOW)

        assert opened == []

    def test_one_mailbox_failing_doesnt_stop_the_others(self, session):
        _first, mailbox = a_pitched_entry(session)
        _second, other = a_pitched_entry(session, thread_id="thread2")

        def open_gmail(box):
            if box.id == mailbox.id:
                raise RuntimeError("no token")
            return FakeGmail()

        outcomes = sync_all(session, open_gmail=open_gmail, now=NOW)

        assert outcomes[mailbox.id].error
        assert outcomes[other.id].error is None
