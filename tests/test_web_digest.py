"""The digest page: tonight's curators to pitch, and one click to record what Jarred did."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from core.models import (
    Artist,
    Curator,
    EmailMessage,
    MailDirection,
    Outreach,
    PlaylistProfileFit,
    PlaylistStatus,
    Profile,
)
from tests.factories import (
    make_contact,
    make_curator,
    make_mailbox,
    make_outreach,
    make_playlist,
    make_profile,
)
from tests.web_helpers import FakeGoogle, csrf_token, sign_in, web_settings
from web.app import create_app
from web.db import get_db

DIGEST_DATE = date(2026, 9, 15)


@pytest.fixture
def client(session):
    app = create_app(web_settings(), identity_provider=FakeGoogle())

    def use_test_session():
        yield session

    app.dependency_overrides[get_db] = use_test_session
    test_client = TestClient(app, follow_redirects=False)
    sign_in(test_client)
    return test_client


def page(client: TestClient, path: str = "/digest"):
    return client.get(path, headers={"accept": "text/html"})


def htmx_post(client: TestClient, path: str, data: dict):
    return client.post(path, data=data, headers={"x-csrf-token": csrf_token(client), "hx-request": "true"})


def an_entry(
    session,
    *,
    profile: Profile | None = None,
    digest_date: date = DIGEST_DATE,
    brief: str = "Nik curates warm IDM.",
    email: str = "nik@valleyview.example",
) -> Outreach:
    """A digested playlist with a reachable curator. Emails must differ: one address, one curator.

    Pass `profile` to put a second entry on the same profile; otherwise each call makes its own.
    """
    profile = profile or make_profile(session)
    curator = make_curator(session, display_name="Nik Davies")
    make_contact(session, curator, route_type="email", value=email)
    playlist = make_playlist(
        session,
        curator=curator,
        name="IDM Ambient Electronica",
        status=PlaylistStatus.DIGESTED,
        followers=2051,
        size_band="2k-10k",
        last_add_at=datetime(2026, 9, 10, tzinfo=UTC),
    )
    session.add(
        PlaylistProfileFit(
            playlist_id=playlist.spotify_id,
            profile_id=profile.id,
            fit_score=0.9,
            reference_artists_present=["Aphex Twin"],
            qualified=True,
        )
    )
    session.flush()
    return make_outreach(
        session,
        curator,
        profile,
        digest_date,
        playlist=playlist,
        brief_text=brief,
        suggested_angle="Open with the warm pads.",
    )


def test_the_navigation_links_to_the_digest(client):
    assert 'href="/digest"' in page(client, "/profiles").text


def test_no_digest_yet_is_explained(client):
    response = page(client)

    assert response.status_code == 200
    assert "No digest yet" in response.text


def test_the_digest_shows_each_entry_ready_to_pitch(client, session):
    outreach = an_entry(session)

    html = page(client).text

    assert "IDM Ambient Electronica" in html
    assert "Nik Davies" in html
    assert "Nik curates warm IDM." in html
    assert "Open with the warm pads." in html
    assert 'href="mailto:nik@valleyview.example"' in html
    assert f'href="https://open.spotify.com/playlist/{outreach.playlist_id}"' in html
    assert f'hx-post="/outreach/{outreach.id}/verdict"' in html
    for verdict in ("pitched", "skip", "bad-fit", "dead"):
        assert f'"verdict": "{verdict}"' in html


def a_reply(session, outreach: Outreach) -> None:
    """A reply from the curator, in the mailbox this artist pitches from."""
    mailbox = make_mailbox(session, session.get(Artist, outreach.profile.artist_id))
    session.add(
        EmailMessage(
            outreach_id=outreach.id,
            mail_account_id=mailbox.id,
            direction=MailDirection.IN,
            gmail_message_id="m1",
            gmail_thread_id="t1",
            from_address="nik@valleyview.example",
            to_address="synman@gmail.com",
            subject="Re: warm pads",
            body_text="Send it over.",
            sent_at=datetime(2026, 9, 15, 11, 0, tzinfo=UTC),
        )
    )
    session.flush()


def test_an_entry_nobody_has_written_to_offers_a_pitch(client, session):
    an_entry(session)

    html = page(client).text

    assert "Write a pitch" in html
    assert "Reply waiting" not in html


def test_a_waiting_reply_shows_without_opening_anything(client, session):
    a_reply(session, an_entry(session))

    html = page(client).text

    assert "Reply waiting" in html
    assert "Read the reply" in html


def test_a_short_digest_says_so_plainly(client, session):
    an_entry(session)

    assert "fewer than 10" in page(client).text


def test_claudes_brief_is_escaped(client, session):
    an_entry(session, brief="<script>alert('x')</script>")

    html = page(client).text

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_an_earlier_digest_by_date_links_to_the_later_one(client, session):
    an_entry(session, digest_date=date(2026, 9, 14), email="older@valleyview.example")
    an_entry(session, digest_date=DIGEST_DATE)

    html = page(client, "/digest/2026-09-14").text

    assert 'href="/digest/2026-09-15"' in html


@pytest.mark.parametrize("path", ["/digest/not-a-date", "/digest/2026-02-30"])
def test_a_bad_date_is_not_found(client, path):
    assert page(client, path).status_code == 404


class TestProfileFilter:
    """Two artists share an admin's page, so it can be narrowed to one profile at a time."""

    def test_the_filter_narrows_the_page_to_one_profile(self, client, session):
        mine = an_entry(session, brief="Synman tonight", email="synman@valleyview.example")
        an_entry(session, brief="NEWIRE tonight", email="newire@valleyview.example")

        html = page(client, f"/digest?profile={mine.profile_id}").text

        assert "Synman tonight" in html
        assert "NEWIRE tonight" not in html

    def test_no_filter_still_shows_everything(self, client, session):
        an_entry(session, brief="Synman tonight", email="synman@valleyview.example")
        an_entry(session, brief="NEWIRE tonight", email="newire@valleyview.example")

        html = page(client).text

        assert "Synman tonight" in html
        assert "NEWIRE tonight" in html

    def test_the_filter_row_is_hidden_when_there_is_only_one_profile(self, client, session):
        an_entry(session)

        assert "Filter by profile" not in page(client).text

    def test_the_filter_row_offers_all_and_marks_the_chosen_profile(self, client, session):
        mine = an_entry(session, email="synman@valleyview.example")
        other = an_entry(session, email="newire@valleyview.example")
        night = DIGEST_DATE.isoformat()

        html = page(client, f"/digest?profile={mine.profile_id}").text

        assert 'aria-label="Filter by profile"' in html
        assert f'href="/digest/{night}"' in html  # All
        assert f'href="/digest/{night}?profile={other.profile_id}"' in html
        assert f'href="/digest/{night}?profile={mine.profile_id}" aria-current="page"' in html

    def test_the_date_links_keep_the_filter(self, client, session):
        mine = an_entry(session, digest_date=date(2026, 9, 14), email="older@valleyview.example")
        an_entry(session, profile=mine.profile, email="newer@valleyview.example")
        an_entry(session, email="other@valleyview.example")  # a second profile, so a filter row renders

        html = page(client, f"/digest/2026-09-14?profile={mine.profile_id}").text

        # Stepping to the next night must not quietly clear the filter.
        assert f'href="/digest/{DIGEST_DATE.isoformat()}?profile={mine.profile_id}"' in html

    def test_a_night_the_chosen_profile_missed_explains_itself(self, client, session):
        mine = an_entry(session, email="synman@valleyview.example")
        an_entry(session, digest_date=date(2026, 9, 14), email="newire@valleyview.example")

        html = page(client, f"/digest/2026-09-14?profile={mine.profile_id}").text

        # "Nothing on this night" reads like a bug when the filter is what emptied the page.
        assert "Nothing for this profile" in html
        assert "Nothing on this night" not in html
        assert ">Show every profile</a>" in html


class TestVerdicts:
    def test_pitched_is_recorded_and_the_entry_updates_in_place(self, client, session):
        outreach = an_entry(session)

        response = htmx_post(client, f"/outreach/{outreach.id}/verdict", {"verdict": "pitched"})

        assert response.status_code == 200
        assert f'id="entry-status-{outreach.id}">' in response.text
        assert "tag--empty" not in response.text  # the tag carries a verdict now, so it shows
        assert "Pitched" in response.text
        assert session.get(Outreach, outreach.id).status == "pitched"
        assert session.get(Outreach, outreach.id).pitched_at is not None

    def test_bad_fit_keeps_the_curator_out_for_good(self, client, session):
        outreach = an_entry(session)

        htmx_post(client, f"/outreach/{outreach.id}/verdict", {"verdict": "bad-fit"})

        assert session.get(Curator, outreach.curator_id).exclusion_reason == "bad-fit"

    def test_an_unknown_verdict_is_refused(self, client, session):
        outreach = an_entry(session)

        response = htmx_post(client, f"/outreach/{outreach.id}/verdict", {"verdict": "loved-it"})

        assert response.status_code == 422
        assert session.get(Outreach, outreach.id).status == "new"

    def test_an_unknown_entry_is_not_found(self, client):
        assert htmx_post(client, "/outreach/999999/verdict", {"verdict": "skip"}).status_code == 404

    def test_a_verdict_needs_the_csrf_token(self, client, session):
        outreach = an_entry(session)

        response = client.post(f"/outreach/{outreach.id}/verdict", data={"verdict": "skip"})

        assert response.status_code == 403
