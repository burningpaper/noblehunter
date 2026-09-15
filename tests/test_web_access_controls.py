"""Positive controls for the route walk: an admin doing the same things does see and change them.

Without these, "nothing changed" and "nothing shown" could pass because the ids, forms or seeded
state were wrong, not because access worked. Every write route in the walk must change the
snapshot when an admin calls it with the same ids and form (Ask Claude's question writes
nothing, so it must reach the suggester instead), and every secret the outsider is denied must
be on the page for the admin.
"""

import pytest

from tests.access_world import (
    NIGHT,
    NIGHT_IN_NUMBERS,
    RUN_ERROR_MARKER,
    THEIR_ARTIST,
    THEIR_BRIEF,
    THEIR_PROFILE,
    admin_client,
    build_world,
    seed_route_state,
    their_data,
)
from tests.route_walk import ROUTES, WRITE_ROUTES, call
from tests.web_helpers import FakeSuggester, csrf_token

ASKS_CLAUDE = ("POST", "/profiles/{profile_id}/suggest/{section}")
HTML = {"accept": "text/html"}
READ_ROUTES = sorted(route for route in ROUTES if route[0] == "GET")
# Pages that show the other artist's profile to someone allowed to see it.
SHOWS_THEIR_PROFILE = {"/profiles", "/profiles/{profile_id}", "/digest", "/digest/{day}"}


@pytest.fixture
def world(session):
    return build_world(session)


@pytest.mark.parametrize(("method", "path"), WRITE_ROUTES)
def test_an_admin_calling_the_same_write_route_does_change_something(session, world, method, path):
    suggester = FakeSuggester()
    admin = admin_client(session, suggester)  # signed in first: signing in writes the user row
    token = csrf_token(admin)
    seed_route_state(session, world, method, path)
    before = their_data(session, world)

    response = call(admin, method, path, world.ids, token=token)

    assert response.status_code == 200, response.text[:300]
    if (method, path) == ASKS_CLAUDE:
        assert len(suggester.calls) == 1
    else:
        assert their_data(session, world) != before


@pytest.mark.parametrize(("method", "path"), READ_ROUTES)
def test_an_admin_opening_the_same_page_gets_it(session, world, method, path):
    # So the outsider's 404 on these ids comes from the access check, not from bad ids.
    admin = admin_client(session)

    response = call(admin, method, path, world.ids, token=csrf_token(admin))

    assert response.status_code == 200, response.text[:300]
    if path in SHOWS_THEIR_PROFILE:
        assert THEIR_PROFILE in response.text


def test_the_read_routes_are_the_seven_gets():
    assert len(READ_ROUTES) == 7
    assert {path for _, path in READ_ROUTES} >= SHOWS_THEIR_PROFILE


def test_the_list_of_write_routes_includes_asking_claude():
    assert ASKS_CLAUDE in WRITE_ROUTES  # otherwise the special case above guards nothing


@pytest.mark.parametrize(
    ("path", "secret"),
    [
        ("/profiles", THEIR_ARTIST),
        ("/profiles", THEIR_PROFILE),
        ("/digest", THEIR_PROFILE),
        ("/digest", THEIR_BRIEF),
        ("/digest", NIGHT_IN_NUMBERS),
        (f"/digest/{NIGHT.isoformat()}", THEIR_BRIEF),
        (f"/digest/{NIGHT.isoformat()}", NIGHT_IN_NUMBERS),
        ("/runs/status", RUN_ERROR_MARKER),
        ("/runs/status", 'hx-post="/runs/request"'),
    ],
)
def test_an_admin_does_see_what_the_outsider_is_denied(session, world, path, secret):
    assert secret in admin_client(session).get(path, headers=HTML).text
