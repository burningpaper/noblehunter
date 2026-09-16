"""Mailboxes belong to an artist, messages belong to a mailbox, and a profile can only use its
own artist's."""

from datetime import date

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from core.models import EmailMessage, MailAccount
from tests.factories import make_artist, make_curator, make_mailbox, make_outreach, make_profile

DIGEST_DATE = date(2026, 9, 16)


def test_the_mail_tables_exist(engine):
    assert {"mail_accounts", "email_messages"} <= set(inspect(engine).get_table_names())


def test_one_address_per_artist(session):
    artist = make_artist(session)
    make_mailbox(session, artist, address="synman@gmail.com")

    with pytest.raises(IntegrityError):
        make_mailbox(session, artist, address="synman@gmail.com")


def test_two_artists_can_connect_the_same_address(session):
    make_mailbox(session, make_artist(session), address="shared@gmail.com")

    assert make_mailbox(session, make_artist(session), address="shared@gmail.com").id is not None


def test_a_profile_cannot_use_another_artists_mailbox(session):
    theirs = make_mailbox(session, make_artist(session))
    profile = make_profile(session, artist=make_artist(session))

    profile.mail_account_id = theirs.id
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_profile_can_use_its_own_artists_mailbox(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    profile = make_profile(session, artist=artist)

    profile.mail_account_id = mailbox.id
    session.flush()

    assert profile.mail_account_id == mailbox.id


def test_a_gmail_message_is_stored_once_per_mailbox(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    entry = make_outreach(session, make_curator(session), make_profile(session, artist=artist), DIGEST_DATE)
    fields = {
        "mail_account_id": mailbox.id,
        "outreach_id": entry.id,
        "gmail_message_id": "m1",
        "gmail_thread_id": "t1",
        "direction": "out",
        "from_address": "synman@gmail.com",
        "to_address": "curator@example.com",
        "subject": "Hello",
        "body_text": "Hi there",
        "sent_at": entry.created_at,
    }
    session.add(EmailMessage(**fields))
    session.flush()

    session.add(EmailMessage(**fields))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_same_gmail_id_can_exist_in_another_mailbox(session):
    artist = make_artist(session)
    profile = make_profile(session, artist=artist)
    entry = make_outreach(session, make_curator(session), profile, DIGEST_DATE)
    for address in ("one@gmail.com", "two@gmail.com"):
        mailbox = make_mailbox(session, artist, address=address)
        session.add(
            EmailMessage(
                mail_account_id=mailbox.id,
                outreach_id=entry.id,
                gmail_message_id="same",
                gmail_thread_id="t1",
                direction="out",
                from_address=address,
                to_address="curator@example.com",
                subject="Hello",
                body_text="Hi",
                sent_at=entry.created_at,
            )
        )
    session.flush()


def test_direction_must_be_in_or_out(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    entry = make_outreach(session, make_curator(session), make_profile(session, artist=artist), DIGEST_DATE)
    session.add(
        EmailMessage(
            mail_account_id=mailbox.id,
            outreach_id=entry.id,
            gmail_message_id="m2",
            gmail_thread_id="t1",
            # Three characters, so the CHECK constraint is what rejects it rather than the
            # column's varchar(3) length limit.
            direction="up",
            from_address="a@b.com",
            to_address="c@d.com",
            subject="",
            body_text="",
            sent_at=entry.created_at,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_one_send_key_across_the_app(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    entry = make_outreach(session, make_curator(session), make_profile(session, artist=artist), DIGEST_DATE)
    for gmail_id in ("m3", "m4"):
        session.add(
            EmailMessage(
                mail_account_id=mailbox.id,
                outreach_id=entry.id,
                gmail_message_id=gmail_id,
                gmail_thread_id="t1",
                direction="out",
                from_address="a@b.com",
                to_address="c@d.com",
                subject="",
                body_text="",
                sent_at=entry.created_at,
                send_key="the-same-key",
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_profile_starts_with_the_default_conversation_settings(session):
    profile = make_profile(session)

    assert (profile.open_conversation_limit, profile.quiet_after_days) == (20, 14)


@pytest.mark.parametrize(("limit", "days"), [(0, 14), (201, 14), (20, 0), (20, 366)])
def test_the_conversation_settings_have_sane_bounds(session, limit, days):
    profile = make_profile(session)

    profile.open_conversation_limit, profile.quiet_after_days = limit, days
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_a_mailbox_detaches_the_profile(session):
    """The composite key must clear only the mailbox pointer, never the artist."""
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    profile = make_profile(session, artist=artist)
    profile.mail_account_id = mailbox.id
    session.flush()

    session.delete(mailbox)
    session.flush()
    session.expire(profile)  # the database cleared the column, not SQLAlchemy

    assert profile.mail_account_id is None
    assert profile.artist_id == artist.id


def test_a_mailbox_is_kept_when_a_profile_lets_go(session):
    artist = make_artist(session)
    mailbox = make_mailbox(session, artist)
    profile = make_profile(session, artist=artist)
    profile.mail_account_id = mailbox.id
    session.flush()

    profile.mail_account_id = None
    session.flush()

    assert session.get(MailAccount, mailbox.id) is not None
