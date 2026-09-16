"""What the digest knows about each entry's mail."""

from datetime import UTC, date, datetime

from core.digest_view import digest_view, entry_view
from core.models import EmailMessage, MailDirection
from tests.factories import (
    admin_viewer,
    make_artist,
    make_curator,
    make_mailbox,
    make_outreach,
    make_playlist,
    make_profile,
)
from tests.query_counting import record_statements

NIGHT = date(2026, 9, 16)
TODAY = date(2026, 9, 17)
SENT_AT = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
REPLIED_AT = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)


def an_entry(session):
    artist = make_artist(session)
    profile = make_profile(session, artist=artist)
    mailbox = make_mailbox(session, artist)
    profile.mail_account_id = mailbox.id
    curator = make_curator(session)
    outreach = make_outreach(
        session, curator, profile, NIGHT, playlist=make_playlist(session, curator=curator)
    )
    outreach.mail_account_id, outreach.gmail_thread_id = mailbox.id, "thread1"
    session.flush()
    return outreach, mailbox


def add_message(session, outreach, mailbox, direction, sent_at, message_id):
    session.add(
        EmailMessage(
            outreach_id=outreach.id,
            mail_account_id=mailbox.id,
            direction=direction,
            gmail_message_id=message_id,
            gmail_thread_id="thread1",
            from_address="a@b.com",
            to_address="c@d.com",
            subject="Kelvin",
            body_text="…",
            sent_at=sent_at,
        )
    )
    session.flush()


def only_entry(session):
    view = digest_view(session, NIGHT, today=TODAY, viewer=admin_viewer())
    return view.profiles[0].entries[0]


def test_an_entry_with_no_mail_is_waiting_on_nobody(session):
    an_entry(session)

    entry = only_entry(session)

    assert (entry.mail.sent, entry.mail.received) == (0, 0)
    assert entry.mail.waiting_on_you is False


def test_a_pitch_we_sent_is_counted(session):
    outreach, mailbox = an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.OUT, SENT_AT, "m1")

    entry = only_entry(session)

    assert (entry.mail.sent, entry.mail.received) == (1, 0)
    assert entry.mail.waiting_on_you is False
    assert entry.mail.last_at == SENT_AT


def test_a_reply_means_it_is_waiting_on_you(session):
    outreach, mailbox = an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.OUT, SENT_AT, "m1")
    add_message(session, outreach, mailbox, MailDirection.IN, REPLIED_AT, "m2")

    entry = only_entry(session)

    assert (entry.mail.sent, entry.mail.received) == (1, 1)
    assert entry.mail.waiting_on_you is True
    assert entry.mail.last_at == REPLIED_AT


def test_answering_the_reply_hands_it_back(session):
    outreach, mailbox = an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.IN, REPLIED_AT, "m2")
    add_message(session, outreach, mailbox, MailDirection.OUT, datetime(2026, 9, 17, 9, 0, tzinfo=UTC), "m3")

    assert only_entry(session).mail.waiting_on_you is False


def test_one_entrys_mail_doesnt_leak_onto_another(session):
    first, mailbox = an_entry(session)
    second, _ = an_entry(session)
    add_message(session, first, mailbox, MailDirection.IN, REPLIED_AT, "m2")

    waiting = {
        entry.outreach_id: entry.mail.waiting_on_you
        for group in digest_view(session, NIGHT, today=TODAY, viewer=admin_viewer()).profiles
        for entry in group.entries
    }

    assert waiting[first.id] is True
    assert waiting[second.id] is False


def test_the_single_entry_view_reads_the_same_state(session):
    outreach, mailbox = an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.IN, REPLIED_AT, "m2")

    assert entry_view(session, outreach.id, today=TODAY).mail.waiting_on_you is True


def test_the_night_reads_mail_in_a_fixed_number_of_queries(session):
    outreach, mailbox = an_entry(session)
    an_entry(session)
    an_entry(session)
    add_message(session, outreach, mailbox, MailDirection.IN, REPLIED_AT, "m2")
    session.commit()

    statements: list[str] = []
    with record_statements(session, statements):
        digest_view(session, NIGHT, today=TODAY, viewer=admin_viewer())

    # Two: one for the counts, one for who spoke last. Three entries, still two -- never per entry.
    assert sum("email_messages" in statement for statement in statements) == 2
