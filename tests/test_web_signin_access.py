"""Signing in needs an admin email or a place on an artist, and access is re-checked on every request."""

from sqlalchemy import delete, select

from core.models import ArtistMember, User
from tests.factories import make_artist, make_member, make_user
from tests.web_helpers import FakeGoogle, app_client, csrf_token, member_client

HTML = {"accept": "text/html"}


def test_a_member_of_an_artist_can_sign_in(session):
    make_member(session, make_artist(session), make_user(session, "nik@example.com"))

    client = member_client(session, "nik@example.com")

    assert client.get("/", headers=HTML).status_code == 200


def test_signing_in_records_the_person(session):
    make_member(session, make_artist(session), make_user(session, "nik@example.com"))

    member_client(session, "Nik@Example.com")

    user = session.scalar(select(User).where(User.email == "nik@example.com"))
    assert user.name == "Owner"  # FakeGoogle's display name
    assert user.last_signed_in_at is not None


def test_an_admin_signs_in_without_a_membership_and_is_recorded(session):
    member_client(session, "owner@example.com")

    assert session.scalar(select(User).where(User.email == "owner@example.com")) is not None


def test_someone_on_no_artist_is_refused(session):
    make_user(session, "former@example.com")
    google = FakeGoogle()
    google.userinfo["email"] = "former@example.com"
    client = app_client(session, google)
    client.get("/auth/google")

    response = client.get("/auth/callback?state=fake&code=fake", headers=HTML)

    assert response.status_code == 403
    assert client.get("/", headers=HTML).status_code == 303


def test_taking_someone_off_their_artist_stops_them_on_the_next_click(session):
    user = make_member(session, make_artist(session), make_user(session, "nik@example.com"))
    client = member_client(session, "nik@example.com")

    session.execute(delete(ArtistMember).where(ArtistMember.user_id == user.id))
    response = client.get("/profiles", headers=HTML)

    assert response.status_code == 403
    assert "not allowed" in response.text.lower()
    assert client.get("/", headers=HTML).status_code == 303  # the session was cleared


def test_an_htmx_request_after_removal_is_sent_back_to_login(session):
    user = make_member(session, make_artist(session), make_user(session, "nik@example.com"))
    client = member_client(session, "nik@example.com")

    session.execute(delete(ArtistMember).where(ArtistMember.user_id == user.id))
    response = client.get("/runs/status", headers={"hx-request": "true"})

    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login"


def test_signing_out_still_works_after_removal(session):
    user = make_member(session, make_artist(session), make_user(session, "nik@example.com"))
    client = member_client(session, "nik@example.com")
    token = csrf_token(client)

    session.execute(delete(ArtistMember).where(ArtistMember.user_id == user.id))
    response = client.post("/logout", headers={"x-csrf-token": token, "hx-request": "true"})

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == "/login"
