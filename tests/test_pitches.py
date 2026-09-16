"""Drafting a pitch, sending it, and reading the thread back."""

import base64
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from core.gmail import GmailError
from core.models import EmailMessage, MailDirection, OutreachStatus
from core.pitches import (
    PitchProblem,
    mark_thread_read,
    pitch_address,
    save_draft,
    send_pitch,
    thread_messages,
)
from tests.factories import (
    make_contact,
    make_curator,
    make_mailbox,
    make_outreach,
    make_playlist,
    make_profile,
)

NIGHT = date(2026, 9, 16)
NOW = datetime(2026, 9, 16, 9, 30, tzinfo=UTC)


class FakeGmail:
    """Stands in for `core.gmail.Gmail`: records what was sent, or raises.

    `Gmail.send` takes a base64url string and a keyword-only thread id, so this does too.
    """

    def __init__(self, error: GmailError | None = None):
        self.sent: list[tuple[str, str | None]] = []
        self.error = error

    def send(self, raw: str, *, thread_id: str | None = None) -> tuple[str, str]:
        if self.error:
            raise self.error
        self.sent.append((raw, thread_id))
        return f"msg{len(self.sent)}", thread_id or "thread1"

    def decoded(self, index: int = 0) -> str:
        return base64.urlsafe_b64decode(self.sent[index][0].encode()).decode()


def an_outreach(session, *, email: str | None = "nina@broken-machines.com"):
    curator = make_curator(session, display_name="Nina")
    if email:
        make_contact(session, curator, "email", email)
    profile = make_profile(session, "IDM Playlists")
    playlist = make_playlist(session, curator=curator, name="Broken Machines")
    return make_outreach(session, curator, profile, NIGHT, playlist=playlist)


def a_sendable(session):
    outreach = an_outreach(session)
    mailbox = make_mailbox(session, outreach.profile.artist, address="synman@gmail.com")
    outreach.profile.mail_account_id = mailbox.id
    session.flush()
    return outreach, mailbox


class TestWhoWeWriteTo:
    def test_it_is_the_curators_best_email(self, session):
        outreach = an_outreach(session)

        assert pitch_address(session, outreach) == "nina@broken-machines.com"

    def test_a_curator_with_no_email_cannot_be_pitched_by_email(self, session):
        outreach = an_outreach(session, email=None)

        with pytest.raises(PitchProblem, match="no email address"):
            pitch_address(session, outreach)

    def test_an_instagram_only_curator_cannot_be_pitched_by_email(self, session):
        curator = make_curator(session)
        make_contact(session, curator, "instagram", "brokenmachines")
        outreach = make_outreach(session, curator, make_profile(session), NIGHT)

        with pytest.raises(PitchProblem, match="no email address"):
            pitch_address(session, outreach)


class TestDrafts:
    def test_a_draft_is_kept_on_the_entry(self, session):
        outreach = an_outreach(session)

        save_draft(session, outreach, subject="Kelvin for Broken Machines", body="Hi Nina", now=NOW)

        assert outreach.draft_subject == "Kelvin for Broken Machines"
        assert outreach.draft_body == "Hi Nina"
        assert outreach.draft_updated_at == NOW

    def test_saving_again_replaces_it(self, session):
        outreach = an_outreach(session)
        save_draft(session, outreach, subject="First", body="One", now=NOW)

        save_draft(session, outreach, subject="Second", body="Two", now=NOW)

        assert (outreach.draft_subject, outreach.draft_body) == ("Second", "Two")

    def test_a_blank_body_is_refused(self, session):
        outreach = an_outreach(session)

        with pytest.raises(PitchProblem, match="Write something"):
            save_draft(session, outreach, subject="Kelvin", body="   ", now=NOW)

    def test_a_blank_subject_is_refused(self, session):
        outreach = an_outreach(session)

        with pytest.raises(PitchProblem, match="subject"):
            save_draft(session, outreach, subject="  ", body="Hi Nina", now=NOW)


def send(session, outreach, mailbox, gmail, **overrides):
    fields = {"subject": "Kelvin", "body": "Hi Nina", "now": NOW, **overrides}
    return send_pitch(session, outreach, gmail=gmail, mailbox=mailbox, **fields)


class TestSending:
    def test_it_sends_the_draft_and_records_the_message(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()

        message = send(
            session,
            outreach,
            mailbox,
            gmail,
            subject="Kelvin for Broken Machines",
            body="Hi Nina,\n\nKelvin...",
        )

        assert gmail.sent[0][1] is None  # a new thread
        assert "nina@broken-machines.com" in gmail.decoded()
        assert "synman@gmail.com" in gmail.decoded()
        assert message.direction == MailDirection.OUT
        assert message.gmail_message_id == "msg1"
        assert message.from_address == "synman@gmail.com"
        assert message.to_address == "nina@broken-machines.com"
        assert message.subject == "Kelvin for Broken Machines"
        assert message.body_text.startswith("Hi Nina,")
        assert message.sent_at == NOW

    def test_sending_marks_the_entry_pitched(self, session):
        outreach, mailbox = a_sendable(session)

        send(session, outreach, mailbox, FakeGmail())

        assert outreach.status == OutreachStatus.PITCHED
        assert outreach.pitched_at == NOW
        assert outreach.gmail_thread_id == "thread1"
        assert outreach.mail_account_id == mailbox.id

    def test_the_draft_is_cleared_once_it_has_been_sent(self, session):
        outreach, mailbox = a_sendable(session)
        save_draft(session, outreach, subject="Kelvin", body="Hi Nina", now=NOW)

        send(session, outreach, mailbox, FakeGmail())

        assert outreach.draft_subject is None
        assert outreach.draft_body is None

    def test_a_reply_of_ours_stays_in_the_same_thread(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()
        send(session, outreach, mailbox, gmail)

        send(session, outreach, mailbox, gmail, subject="Re: Kelvin", body="Thanks Nina")

        assert gmail.sent[1][1] == "thread1"

    def test_it_replies_to_the_curators_last_message(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()
        send(session, outreach, mailbox, gmail)
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox.id,
                direction=MailDirection.IN,
                gmail_message_id="reply1",
                gmail_thread_id="thread1",
                rfc822_message_id="<nina-1@mail.gmail.com>",
                from_address="nina@broken-machines.com",
                to_address="synman@gmail.com",
                subject="Re: Kelvin",
                body_text="Send it over.",
                sent_at=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
            )
        )
        session.flush()

        send(session, outreach, mailbox, gmail, subject="Re: Kelvin", body="Here it is")

        # The reply is the fake's second message; a fresh fake would reissue "msg1" and collide.
        assert "In-Reply-To: <nina-1@mail.gmail.com>" in gmail.decoded(1)

    def test_gmail_refusing_leaves_the_entry_untouched(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail(error=GmailError("auth", "Google refused the saved Gmail access"))

        with pytest.raises(GmailError):
            send(session, outreach, mailbox, gmail)

        assert outreach.status == OutreachStatus.NEW
        assert outreach.gmail_thread_id is None
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 0

    def test_sending_needs_a_mailbox_and_an_address(self, session):
        outreach = an_outreach(session, email=None)
        mailbox = make_mailbox(session, outreach.profile.artist)

        with pytest.raises(PitchProblem, match="no email address"):
            send(session, outreach, mailbox, FakeGmail())

    def test_a_curator_you_ruled_out_is_never_pitched(self, session):
        outreach, mailbox = a_sendable(session)
        outreach.curator.excluded_at = NOW
        outreach.curator.exclusion_reason = OutreachStatus.BAD_FIT
        session.flush()
        gmail = FakeGmail()

        with pytest.raises(PitchProblem, match="ruled this curator out"):
            send(session, outreach, mailbox, gmail)

        assert gmail.sent == []

    def test_an_entry_already_marked_bad_fit_is_not_downgraded(self, session):
        outreach, mailbox = a_sendable(session)
        outreach.status = OutreachStatus.BAD_FIT
        session.flush()

        with pytest.raises(PitchProblem):
            send(session, outreach, mailbox, FakeGmail())

        assert outreach.status == OutreachStatus.BAD_FIT

    def test_the_same_send_key_twice_sends_once(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()
        send(session, outreach, mailbox, gmail, send_key="abc123")

        with pytest.raises(PitchProblem, match="already been sent"):
            send(session, outreach, mailbox, gmail, send_key="abc123")

        assert len(gmail.sent) == 1


class TestTheThread:
    def test_it_reads_back_in_the_order_it_happened(self, session):
        outreach, mailbox = a_sendable(session)
        send(session, outreach, mailbox, FakeGmail())
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox.id,
                direction=MailDirection.IN,
                gmail_message_id="reply1",
                gmail_thread_id="thread1",
                from_address="nina@broken-machines.com",
                to_address="synman@gmail.com",
                subject="Re: Kelvin",
                body_text="Send it over.",
                sent_at=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
            )
        )
        session.flush()

        messages = thread_messages(session, outreach)

        assert [(m.direction, m.body_text) for m in messages] == [
            (MailDirection.OUT, "Hi Nina"),
            (MailDirection.IN, "Send it over."),
        ]

    def test_an_entry_with_no_mail_has_an_empty_thread(self, session):
        assert thread_messages(session, an_outreach(session)) == []

    def test_opening_a_thread_marks_their_messages_read(self, session):
        outreach, mailbox = a_sendable(session)
        send(session, outreach, mailbox, FakeGmail())
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox.id,
                direction=MailDirection.IN,
                gmail_message_id="reply1",
                gmail_thread_id="thread1",
                from_address="nina@broken-machines.com",
                to_address="synman@gmail.com",
                subject="Re: Kelvin",
                body_text="Send it over.",
                sent_at=NOW,
            )
        )
        session.flush()

        marked = mark_thread_read(session, outreach, now=NOW)

        assert marked == 1
        assert mark_thread_read(session, outreach, now=NOW) == 0  # nothing left to mark
        ours = [m for m in thread_messages(session, outreach) if m.direction == MailDirection.OUT]
        assert all(message.read_at is None for message in ours)  # our own were never unread
