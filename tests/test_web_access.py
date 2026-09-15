"""Every route, walked by someone on a different artist: nothing of the other artist's can be seen or changed.

The route table lives in tests/route_walk.py and the world in tests/access_world.py. Status
codes alone aren't trusted: a write route could answer 404 after it had already committed, so
each write route gets a fresh world and a before/after snapshot of everything the other artist
owns. tests/test_web_access_controls.py proves each of those routes can change the snapshot.
"""

import pytest
from fastapi import FastAPI

from core.run_status import MEMBER_WARNING
from tests.access_world import (
    MY_BRIEF,
    MY_PROFILE,
    NIGHT,
    NIGHT_IN_NUMBERS,
    RUN_ERROR_MARKER,
    THEIR_ARTIST,
    THEIR_BRIEF,
    THEIR_PROFILE,
    build_world,
    outsider_client,
    seed_route_state,
    their_data,
)
from tests.route_walk import EXEMPT, NOT_FOUND, ROUTES, WRITE_ROUTES, call, registered_routes
from tests.web_helpers import FakeSuggester, csrf_token

OUTSIDER_PAGES = ["/", "/profiles", "/digest", f"/digest/{NIGHT.isoformat()}", "/runs/status"]
HTML = {"accept": "text/html"}


@pytest.fixture
def world(session):
    return build_world(session)


@pytest.fixture
def suggester():
    return FakeSuggester()


@pytest.fixture
def outsider(session, world, suggester):
    return outsider_client(session, suggester)


def test_every_route_is_covered(outsider):
    registered = registered_routes(outsider.app)

    assert registered - EXEMPT == set(ROUTES)
    assert EXEMPT <= registered  # an exempt route that no longer exists should leave the list too


# "/static" too: skipping the static mount must also require it to be StaticFiles.
@pytest.mark.parametrize("mount_path", ["/admin", "/static"])
def test_a_mounted_sub_app_fails_the_coverage_check_instead_of_being_skipped(mount_path):
    no_docs = {"docs_url": None, "redoc_url": None, "openapi_url": None}  # only the mount to find
    app = FastAPI(**no_docs)
    sub_app = FastAPI(**no_docs)

    @sub_app.get("/secrets")
    def secrets() -> dict:
        return {}

    app.mount(mount_path, sub_app)

    with pytest.raises(AssertionError, match=f"'{mount_path}'"):
        registered_routes(app)


@pytest.mark.parametrize(("method", "path"), sorted(ROUTES))
def test_an_outsider_gets_the_expected_answer(world, outsider, method, path):
    response = call(outsider, method, path, world.ids, token=csrf_token(outsider))

    assert response.status_code == ROUTES[(method, path)], response.text[:300]


@pytest.mark.parametrize(
    ("method", "path"), sorted(route for route, status in ROUTES.items() if status == NOT_FOUND)
)
def test_their_ids_and_ids_that_dont_exist_get_identical_answers(world, outsider, method, path):
    token = csrf_token(outsider)
    missing = {
        key: value + 1_000_000 if isinstance(value, int) else value for key, value in world.ids.items()
    }

    for_theirs = call(outsider, method, path, world.ids, token=token)
    for_missing = call(outsider, method, path, missing, token=token)

    assert for_theirs.status_code == for_missing.status_code == NOT_FOUND
    assert for_theirs.text == for_missing.text


@pytest.mark.parametrize(("method", "path"), WRITE_ROUTES)
def test_a_write_route_changes_nothing_of_theirs(session, world, outsider, method, path):
    token = csrf_token(outsider)
    seed_route_state(session, world, method, path)
    before = their_data(session, world)

    call(outsider, method, path, world.ids, token=token)

    assert their_data(session, world) == before


def test_walking_every_route_never_asks_claude(world, outsider, suggester):
    token = csrf_token(outsider)

    for method, path in sorted(ROUTES):
        call(outsider, method, path, world.ids, token=token)

    assert suggester.calls == []


@pytest.mark.parametrize("path", OUTSIDER_PAGES)
def test_pages_an_outsider_can_open_show_nothing_of_theirs(outsider, path):
    html = outsider.get(path, headers=HTML).text

    for secret in (THEIR_ARTIST, THEIR_PROFILE, THEIR_BRIEF, RUN_ERROR_MARKER):
        assert secret not in html


@pytest.mark.parametrize(("path", "own_content"), [("/profiles", MY_PROFILE), ("/digest", MY_BRIEF)])
def test_an_outsiders_own_pages_show_their_own_content(outsider, path, own_content):
    assert own_content in outsider.get(path, headers=HTML).text


@pytest.mark.parametrize("path", ["/digest", f"/digest/{NIGHT.isoformat()}"])
def test_an_outsider_doesnt_see_the_night_in_numbers(outsider, path):
    assert NIGHT_IN_NUMBERS not in outsider.get(path, headers=HTML).text


@pytest.mark.parametrize("path", ["/profiles", "/runs/status"])
def test_an_outsider_sees_only_the_generic_run_warning_and_no_run_now(outsider, path):
    html = outsider.get(path, headers=HTML).text

    assert MEMBER_WARNING in html
    assert 'hx-post="/runs/request"' not in html
    assert "Run now" not in html
