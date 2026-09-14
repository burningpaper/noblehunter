"""The digest page: tonight's curators to pitch, and one click to record what Jarred did."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from core.models import Curator, Outreach, PlaylistProfileFit, PlaylistStatus
from tests.factories import make_contact, make_curator, make_outreach, make_playlist, make_profile
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
    digest_date: date = DIGEST_DATE,
    brief: str = "Nik curates warm IDM.",
    email: str = "nik@valleyview.example",
) -> Outreach:
    """A digested playlist with a reachable curator. Emails must differ: one address, one curator."""
    profile = make_profile(session)
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


class TestVerdicts:
    def test_pitched_is_recorded_and_the_entry_updates_in_place(self, client, session):
        outreach = an_entry(session)

        response = htmx_post(client, f"/outreach/{outreach.id}/verdict", {"verdict": "pitched"})

        assert response.status_code == 200
        assert 'class="tag tag--status">Pitched' in response.text
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
