"""Every conversation the viewer can see, the ones waiting on them first."""

from datetime import UTC, date, datetime, timedelta

from core.inbox_view import inbox_view
from core.models import EmailMessage, MailDirection
from core.pitches import mark_thread_read
from tests.factories import (
    admin_viewer,
    make_artist,
    make_curator,
    make_mailbox,
    make_outreach,
    make_playlist,
    make_profile,
    member_viewer,
)

NIGHT = date(2026, 9, 16)
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def a_conversation(session, *, artist=None, curator_name="Nina", messages=()):
    artist = artist or make_artist(session)
    profile = make_profile(session, artist=artist)
    mailbox = make_mailbox(session, artist)
    profile.mail_account_id = mailbox.id
    curator = make_curator(session, display_name=curator_name)
    outreach = make_outreach(
        session, curator, profile, NIGHT, playlist=make_playlist(session, curator=curator)
    )
    outreach.mail_account_id, outreach.gmail_thread_id = mailbox.id, f"thread{outreach.id}"
    session.flush()
    for index, (direction, sent_at, body) in enumerate(messages):
        session.add(
            EmailMessage(
                outreach_id=outreach.id,
                mail_account_id=mailbox.id,
                direction=direction,
                gmail_message_id=f"{outreach.id}-{index}",
                gmail_thread_id=outreach.gmail_thread_id,
                from_address="a@b.com",
                to_address="c@d.com",
                subject="Kelvin",
                body_text=body,
                sent_at=sent_at,
            )
        )
    session.flush()
    return outreach


def days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def test_an_entry_never_pitched_is_not_a_conversation(session):
    a_conversation(session)

    assert inbox_view(session, admin_viewer(), now=NOW).conversations == ()


def test_a_sent_pitch_is_a_conversation_waiting_on_them(session):
    a_conversation(session, messages=[(MailDirection.OUT, days_ago(2), "Hi Nina")])

    conversation = inbox_view(session, admin_viewer(), now=NOW).conversations[0]

    assert conversation.waiting_on_you is False
    assert conversation.curator_name == "Nina"
    assert conversation.last_snippet == "Hi Nina"
    assert conversation.days_since_last == 2


def test_a_reply_puts_it_at_the_top(session):
    a_conversation(session, curator_name="Quiet", messages=[(MailDirection.OUT, days_ago(1), "Hi")])
    a_conversation(
        session,
        curator_name="Replied",
        messages=[(MailDirection.OUT, days_ago(5), "Hi"), (MailDirection.IN, days_ago(4), "Send it")],
    )

    names = [c.curator_name for c in inbox_view(session, admin_viewer(), now=NOW).conversations]

    assert names == ["Replied", "Quiet"]


def test_within_a_group_the_most_recent_comes_first(session):
    a_conversation(session, curator_name="Older", messages=[(MailDirection.OUT, days_ago(9), "Hi")])
    a_conversation(session, curator_name="Newer", messages=[(MailDirection.OUT, days_ago(1), "Hi")])

    names = [c.curator_name for c in inbox_view(session, admin_viewer(), now=NOW).conversations]

    assert names == ["Newer", "Older"]


def test_a_member_sees_only_their_own_artists_conversations(session):
    mine = make_artist(session)
    theirs = make_artist(session)
    a_conversation(
        session, artist=mine, curator_name="Mine", messages=[(MailDirection.IN, days_ago(1), "Hi")]
    )
    a_conversation(
        session, artist=theirs, curator_name="Theirs", messages=[(MailDirection.IN, days_ago(1), "Hi")]
    )

    view = inbox_view(session, member_viewer(mine), now=NOW)

    assert [c.curator_name for c in view.conversations] == ["Mine"]


def test_it_counts_how_many_are_waiting_on_you(session):
    a_conversation(session, messages=[(MailDirection.IN, days_ago(1), "Send it")])
    a_conversation(session, messages=[(MailDirection.OUT, days_ago(1), "Hi")])

    view = inbox_view(session, admin_viewer(), now=NOW)

    assert view.waiting == 1
    assert view.total == 2


def test_unread_counts_what_they_sent_and_you_havent_opened(session):
    a_conversation(
        session,
        messages=[(MailDirection.OUT, days_ago(2), "Hi"), (MailDirection.IN, days_ago(1), "Send it")],
    )

    view = inbox_view(session, admin_viewer(), now=NOW)

    assert view.conversations[0].unread == 1
    assert view.unread == 1


def test_opening_the_thread_clears_the_unread_count(session):
    outreach = a_conversation(session, messages=[(MailDirection.IN, days_ago(1), "Send it")])
    mark_thread_read(session, outreach, now=NOW)

    assert inbox_view(session, admin_viewer(), now=NOW).unread == 0


def test_the_filter_narrows_to_one_profile(session):
    artist = make_artist(session)
    mine = a_conversation(
        session, artist=artist, curator_name="Mine", messages=[(MailDirection.OUT, days_ago(1), "Hi")]
    )
    a_conversation(
        session, artist=artist, curator_name="Other", messages=[(MailDirection.OUT, days_ago(1), "Hi")]
    )

    view = inbox_view(session, admin_viewer(), now=NOW, profile_id=mine.profile_id)

    assert [c.curator_name for c in view.conversations] == ["Mine"]
    assert len(view.profiles) == 2  # the chips still offer both
    assert view.chosen_profile_id == mine.profile_id


def test_a_long_reply_is_shown_as_a_snippet(session):
    a_conversation(session, messages=[(MailDirection.IN, days_ago(1), "y" * 400)])

    snippet = inbox_view(session, admin_viewer(), now=NOW).conversations[0].last_snippet

    assert len(snippet) < 200
    assert snippet.endswith("…")


def test_the_artist_is_named_when_the_viewer_has_more_than_one(session):
    first, second = make_artist(session, "First"), make_artist(session, "Second")
    a_conversation(session, artist=first, messages=[(MailDirection.OUT, days_ago(1), "Hi")])
    a_conversation(session, artist=second, messages=[(MailDirection.OUT, days_ago(1), "Hi")])

    assert inbox_view(session, member_viewer(first, second), now=NOW).show_artists is True
    assert inbox_view(session, member_viewer(first), now=NOW).show_artists is False
