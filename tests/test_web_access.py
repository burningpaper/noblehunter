"""Every route, walked by someone on a different artist: nothing of the other artist's can be seen or changed.

The route table lives in tests/route_walk.py. Status codes alone aren't trusted: a write route
could answer 404 after it had already committed, so the walk also compares a snapshot of
everything the other artist owns (and every shared setting) before and after.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from core.app_settings import nightly_claude_budget
from core.models import (
    AntiSignal,
    Artist,
    ArtistMember,
    Curator,
    Outreach,
    Profile,
    ProfileGenre,
    ProfileTrack,
    ReferenceArtist,
    Run,
    RunRequest,
    SearchTerm,
    User,
)
from core.run_status import MEMBER_WARNING
from tests.factories import make_artist, make_curator, make_member, make_outreach, make_profile, make_user
from tests.profile_helpers import add_contents
from tests.route_walk import EXEMPT, NOT_FOUND, ROUTES, call, registered_routes
from tests.web_helpers import FakeSuggester, app_client, csrf_token, member_client, sign_in

NIGHT = date(2026, 9, 15)
THEIR_ARTIST, THEIR_PROFILE, THEIR_BRIEF = "Their Artist", "Their Profile", "Their secret brief"
RUN_ERROR_MARKER = "raw-run-error-naming-their-playlist"
OUTSIDER_PAGES = ["/", "/profiles", "/digest", f"/digest/{NIGHT.isoformat()}", "/runs/status"]
HTML = {"accept": "text/html"}


@dataclass
class World:
    client: TestClient
    suggester: FakeSuggester
    ids: dict
    profile_id: int
    outreach_id: int
    artist_id: int


@pytest.fixture
def world(session) -> World:
    """Another artist with a full profile, a digest entry and a failed run; a member of a different artist."""
    theirs = make_artist(session, THEIR_ARTIST)
    profile = add_contents(session, make_profile(session, THEIR_PROFILE, artist=theirs))
    session.add(AntiSignal(profile=profile, kind="term", value="lofi"))
    session.flush()
    owner = make_member(session, theirs, make_user(session, "them@example.com"))
    outreach = make_outreach(session, make_curator(session), profile, NIGHT, brief_text=THEIR_BRIEF)
    finished = datetime.now(UTC) - timedelta(hours=1)
    session.add(
        Run(
            trigger="schedule",
            status="failed",
            started_at=finished - timedelta(minutes=5),
            finished_at=finished,
            error=f"Timed out reading {RUN_ERROR_MARKER}",
        )
    )
    session.flush()

    make_member(session, make_artist(session, "My Artist"), make_user(session, "me@example.com"))
    suggester = FakeSuggester()
    client = member_client(session, "me@example.com", suggester=suggester)
    ids = {
        "profile_id": profile.id,
        "genre_id": profile.genres[0].id,
        "reference_artist_id": profile.reference_artists[0].id,
        "signal_id": profile.anti_signals[0].id,
        "track_id": profile.tracks[0].id,
        "term_id": profile.search_terms[0].id,
        "section": "genres",
        "day": NIGHT.isoformat(),
        "outreach_id": outreach.id,
        "artist_id": theirs.id,
        "user_id": owner.id,
    }
    return World(client, suggester, ids, profile.id, outreach.id, theirs.id)


def their_data(session, world: World):
    """Everything of theirs a route could change, plus the settings and people every artist shares."""
    session.expire_all()
    profile = session.get(Profile, world.profile_id)
    outreach = session.get(Outreach, world.outreach_id)
    curator = session.get(Curator, outreach.curator_id)
    pid = world.profile_id
    return {
        "profile": (profile.name, profile.is_active, profile.digest_target, profile.min_followers),
        "their_profiles": sorted(
            session.scalars(select(Profile.name).where(Profile.artist_id == world.artist_id))
        ),
        "genres": [
            (g.tag, g.priority)
            for g in session.scalars(
                select(ProfileGenre).where(ProfileGenre.profile_id == pid).order_by(ProfileGenre.priority)
            )
        ],
        "reference_artists": sorted(
            session.scalars(select(ReferenceArtist.display_name).where(ReferenceArtist.profile_id == pid))
        ),
        "anti_signals": sorted(
            (s.kind, s.value) for s in session.scalars(select(AntiSignal).where(AntiSignal.profile_id == pid))
        ),
        "tracks": sorted(
            (t.title, t.spotify_url, t.description)
            for t in session.scalars(select(ProfileTrack).where(ProfileTrack.profile_id == pid))
        ),
        "terms": sorted(
            (t.term, t.status, t.origin)
            for t in session.scalars(select(SearchTerm).where(SearchTerm.profile_id == pid))
        ),
        "outreach": (outreach.status, outreach.status_changed_at, outreach.pitched_at, outreach.notes),
        "curator": (curator.excluded_at, curator.exclusion_reason),
        "budget": nightly_claude_budget(session),
        "artists": sorted(
            (artist.name, sorted(member.email for member in _members(session, artist.id)))
            for artist in session.scalars(select(Artist))
        ),
        "users": sorted(session.scalars(select(User.email))),
        "run_requests": session.scalar(select(func.count()).select_from(RunRequest)),
    }


def _members(session, artist_id: int):
    return session.scalars(select(User).join(ArtistMember).where(ArtistMember.artist_id == artist_id))


def admin_client(session) -> TestClient:
    client = app_client(session)
    sign_in(client)
    return client


def test_every_route_is_covered(world):
    registered = registered_routes(world.client.app)

    assert registered - EXEMPT == set(ROUTES)
    assert EXEMPT <= registered  # an exempt route that no longer exists should leave the list too


@pytest.mark.parametrize(("method", "path"), sorted(ROUTES))
def test_an_outsider_gets_the_expected_answer(world, method, path):
    response = call(world.client, method, path, world.ids, token=csrf_token(world.client))

    assert response.status_code == ROUTES[(method, path)], response.text[:300]


@pytest.mark.parametrize(
    ("method", "path"), sorted(route for route, status in ROUTES.items() if status == NOT_FOUND)
)
def test_their_ids_and_ids_that_dont_exist_get_identical_answers(world, method, path):
    token = csrf_token(world.client)
    missing = {
        key: value + 1_000_000 if isinstance(value, int) else value for key, value in world.ids.items()
    }

    for_theirs = call(world.client, method, path, world.ids, token=token)
    for_missing = call(world.client, method, path, missing, token=token)

    assert for_theirs.status_code == for_missing.status_code == NOT_FOUND
    assert for_theirs.text == for_missing.text


def test_walking_every_route_changes_nothing_of_theirs(world, session):
    token = csrf_token(world.client)
    before = their_data(session, world)

    for method, path in sorted(ROUTES):
        call(world.client, method, path, world.ids, token=token)

    assert their_data(session, world) == before
    assert world.suggester.calls == []


@pytest.mark.parametrize("path", OUTSIDER_PAGES)
def test_pages_an_outsider_can_open_show_nothing_of_theirs(world, path):
    html = world.client.get(path, headers=HTML).text

    for secret in (THEIR_ARTIST, THEIR_PROFILE, THEIR_BRIEF, RUN_ERROR_MARKER):
        assert secret not in html


@pytest.mark.parametrize("path", ["/profiles", "/runs/status"])
def test_an_outsider_sees_only_the_generic_run_warning_and_no_run_now(world, path):
    html = world.client.get(path, headers=HTML).text

    assert MEMBER_WARNING in html
    assert 'hx-post="/runs/request"' not in html
    assert "Run now" not in html


def test_an_admin_does_see_what_the_outsider_is_denied(world, session):
    # Proves the pages above really carry these strings for someone allowed to see them,
    # so their absence for the outsider means something.
    client = admin_client(session)

    assert THEIR_ARTIST in client.get("/profiles", headers=HTML).text
    assert THEIR_BRIEF in client.get(f"/digest/{NIGHT.isoformat()}", headers=HTML).text
    status_panel = client.get("/runs/status", headers=HTML).text
    assert RUN_ERROR_MARKER in status_panel
    assert 'hx-post="/runs/request"' in status_panel
