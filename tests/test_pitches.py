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


def a_sendable(session, **kwargs):
    outreach = an_outreach(session, **kwargs)
    mailbox = make_mailbox(session, outreach.profile.artist, address="synman@gmail.com")
    outreach.profile.mail_account_id = mailbox.id
    session.flush()
    return outreach, mailbox


# The same address, written the three ways research actually scrapes it off a page.
AS_SCRAPED = ["<Nina@B.com>", "Nina@B.com.", "mailto:nina@b.com"]


def spoil_the_stored_address(session, outreach) -> None:
    """Leave the contact's key alone and make its value unusable as an address.

    Research derives `contact_key` from `value` once, at the moment it stores the contact, and
    never looks at the pair again -- so nothing keeps them agreeing afterwards. This is the
    shape of the row that results, and the reason `pitch_address` has to check rather than trust.
    """
    outreach.curator.contacts[0].value = "Nina <nina@b.com>"
    session.flush()


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

    @pytest.mark.parametrize("scraped", AS_SCRAPED)
    def test_the_address_is_tidied_the_same_way_the_dedup_key_is(self, session, scraped):
        # Research stores `value` exactly as it came off the page and normalizes only
        # `contact_key`. Every one of these is already recognised as this curator's address for
        # deduplication; the address we write to has to agree with that, not with the scrape.
        outreach = an_outreach(session, email=scraped)

        assert pitch_address(session, outreach) == "nina@b.com"

    def test_a_stored_value_that_is_not_an_address_is_refused(self, session):
        outreach = an_outreach(session)
        spoil_the_stored_address(session, outreach)

        with pytest.raises(PitchProblem, match="usable email address"):
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

    @pytest.mark.parametrize("scraped", AS_SCRAPED)
    def test_it_writes_to_the_tidied_address_not_the_scraped_one(self, session, scraped):
        # Asserted on the message that actually reached Gmail, because that is the thing a
        # curator's mail server sees. A `To:` of `<Nina@B.com>.` bounces or lands wrong.
        outreach, mailbox = a_sendable(session, email=scraped)
        gmail = FakeGmail()

        message = send(session, outreach, mailbox, gmail)

        assert "To: nina@b.com" in gmail.decoded()
        assert scraped not in gmail.decoded()
        assert message.to_address == "nina@b.com"

    def test_an_address_that_cannot_be_parsed_sends_nothing(self, session):
        outreach, mailbox = a_sendable(session)
        spoil_the_stored_address(session, outreach)
        gmail = FakeGmail()

        with pytest.raises(PitchProblem, match="usable email address"):
            send(session, outreach, mailbox, gmail)

        assert gmail.sent == []
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 0

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


# The subject a real pitch was sitting in the database with, one keystroke from a real curator.
# It was drafted before the writer learned to clean its own output, which is exactly why the
# guard cannot live only where drafts are created.
STORED_ARTIFACT_SUBJECT = "Human Error cdot for Neoclassical Music Gems"


class TestTheSubjectIsCleanedOnTheWayOut:
    """The last gate before Gmail, not just the moment a draft is written.

    Every assertion here reads the message that actually reached the fake, because that is the
    thing a curator's mail client renders. A return value agreeing with itself proves nothing.
    """

    def test_a_draft_stored_before_the_guard_existed_is_cleaned_before_it_is_sent(self, session):
        outreach, mailbox = a_sendable(session)
        # Set straight onto the entry rather than through `save_draft`: this is the shape of the
        # row production is holding, written before any cleaning existed. The panel re-renders
        # it into the box and posts it straight back, which is what `send` receives.
        outreach.draft_subject = STORED_ARTIFACT_SUBJECT
        outreach.draft_body = "Hi Nina"
        session.flush()
        gmail = FakeGmail()

        message = send(
            session,
            outreach,
            mailbox,
            gmail,
            subject=outreach.draft_subject,
            body=outreach.draft_body,
        )

        assert "To: nina@broken-machines.com" in gmail.decoded()
        assert "Subject: Human Error for Neoclassical Music Gems" in gmail.decoded()
        assert "cdot" not in gmail.decoded()
        assert message.subject == "Human Error for Neoclassical Music Gems"

    def test_a_real_title_goes_out_exactly_as_it_was_written(self, session):
        # Guards the fix rather than the bug: "Bullet" is a LaTeX command name in lowercase and
        # a track's name in title case. A guard that matched case-insensitively would send this
        # pitch with the track missing from the subject, which is a worse email than a stray word.
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()

        message = send(
            session,
            outreach,
            mailbox,
            gmail,
            subject="Bullet Train for Broken Machines",
            body="Hi Nina",
        )

        assert "Subject: Bullet Train for Broken Machines" in gmail.decoded()
        assert message.subject == "Bullet Train for Broken Machines"

    def test_a_subject_that_is_nothing_but_an_artifact_sends_nothing(self, session):
        # Cleaning can empty a subject, and an empty Subject header is worse than a stray word:
        # it is a cold email with nothing on the line that decides whether it is opened. So the
        # emptiness check runs after the cleaning, and this fails closed on the existing message.
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()

        with pytest.raises(PitchProblem, match="Give the email a subject"):
            send(session, outreach, mailbox, gmail, subject="cdot", body="Hi Nina")

        assert gmail.sent == []
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 0

    def test_the_body_is_left_exactly_as_the_person_wrote_it(self, session):
        # Deliberate asymmetry. The body is long-form prose the musician reads and edits before
        # anything is sent, so a stray word there is visible and harmless; silently deleting from
        # someone's writing is the worse failure.
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()
        body = "Hi Nina,\n\nIt sits somewhere between a cdot and a comma.\n\nSynman"

        message = send(
            session,
            outreach,
            mailbox,
            gmail,
            subject="Kelvin for Broken Machines",
            body=body,
        )

        assert "cdot" in gmail.decoded()
        assert message.body_text == body


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


class TestGradesAndStatuses:
    def test_an_address_nobody_corroborated_is_not_written_to(self, session):
        """Grade C means only the name matched. The rest of the app won't use it; nor will this."""
        curator = make_curator(session, display_name="Nina")
        contact = make_contact(session, curator, "email", "maybe-nina@example.com")
        contact.confidence = "C"
        session.flush()
        outreach = make_outreach(session, curator, make_profile(session), NIGHT)

        with pytest.raises(PitchProblem, match="no email address"):
            pitch_address(session, outreach)

    def test_a_follow_up_does_not_walk_a_further_entry_backwards(self, session):
        outreach, mailbox = a_sendable(session)
        gmail = FakeGmail()
        send(session, outreach, mailbox, gmail)
        outreach.status = OutreachStatus.REPLIED
        session.flush()

        send(session, outreach, mailbox, gmail, subject="Re: Kelvin", body="Following up")

        assert outreach.status == OutreachStatus.REPLIED
