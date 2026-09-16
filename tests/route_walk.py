"""The route table the access walks share: every route, what an outsider gets, and how to call it.

A new route must be added to ROUTES, or test_every_route_is_covered fails. That is the point:
access can't be forgotten quietly.
"""

import re

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from core.access import MAX_POSTGRES_INT
from tests.profile_helpers import TRACK_URL

OK, FORBIDDEN, NOT_FOUND = 200, 403, 404
OUT_OF_RANGE = MAX_POSTGRES_INT + 1
STATIC_MOUNT = "/static"

# Usable without access to any artist: signing in and out, and the health check.
EXEMPT = {
    ("GET", "/login"),
    ("GET", "/auth/google"),
    ("GET", "/auth/callback"),
    ("POST", "/logout"),
    ("GET", "/health"),
}

# What a signed-in member of a *different* artist gets, using the other artist's real ids.
ROUTES = {
    ("GET", "/"): OK,
    ("GET", "/profiles"): OK,
    ("POST", "/profiles"): NOT_FOUND,
    ("GET", "/profiles/{profile_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/settings"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/activate"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/pause"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/genres"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/genres/{genre_id}/move"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/genres/{genre_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/artists"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/artists/{artist_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/anti-signals"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/anti-signals/{signal_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/tracks"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/tracks/{track_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/terms"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/terms/{term_id}/status"): NOT_FOUND,
    ("DELETE", "/profiles/{profile_id}/terms/{term_id}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/suggest/{section}"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/suggest/{section}/add"): NOT_FOUND,
    ("GET", "/profiles/{profile_id}/mail/connect"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/mail/attach"): NOT_FOUND,
    ("POST", "/profiles/{profile_id}/mail/disconnect"): NOT_FOUND,
    ("GET", "/mail/callback"): NOT_FOUND,
    ("GET", "/digest"): OK,
    ("GET", "/digest/{day}"): OK,
    ("POST", "/outreach/{outreach_id}/verdict"): NOT_FOUND,
    ("GET", "/outreach/{outreach_id}/pitch"): NOT_FOUND,
    ("POST", "/outreach/{outreach_id}/pitch/write"): NOT_FOUND,
    ("POST", "/outreach/{outreach_id}/pitch/save"): NOT_FOUND,
    ("POST", "/outreach/{outreach_id}/pitch/send"): NOT_FOUND,
    ("GET", "/inbox"): OK,
    ("POST", "/inbox/check"): OK,
    ("GET", "/runs/status"): OK,
    ("POST", "/runs/request"): FORBIDDEN,
    ("POST", "/settings/claude-budget"): FORBIDDEN,
    ("GET", "/people"): FORBIDDEN,
    ("POST", "/people/members"): FORBIDDEN,
    ("POST", "/people/artists/{artist_id}/rename"): FORBIDDEN,
    ("POST", "/people/artists/{artist_id}/members/{user_id}/remove"): FORBIDDEN,
}
WRITE_ROUTES = sorted(route for route in ROUTES if route[0] != "GET")

# Valid forms, so a route that forgot its access check would actually do something.
FORMS = {
    "/profiles": {"name": "Stolen", "digest_target": "10"},
    "/profiles/{profile_id}/settings": {"name": "Renamed", "digest_target": "10", "min_followers": "0"},
    "/profiles/{profile_id}/genres": {"tag": "Stolen"},
    "/profiles/{profile_id}/genres/{genre_id}/move": {"direction": "down"},
    "/profiles/{profile_id}/artists": {"name": "Stolen"},
    "/profiles/{profile_id}/anti-signals": {"kind": "term", "value": "stolen"},
    "/profiles/{profile_id}/tracks": {"title": "Stolen", "spotify_url": TRACK_URL, "description": ""},
    "/profiles/{profile_id}/terms": {"term": "stolen"},
    "/profiles/{profile_id}/terms/{term_id}/status": {"status": "paused"},
    "/profiles/{profile_id}/suggest/{section}": {"prompt": "More like this"},
    "/profiles/{profile_id}/suggest/{section}/add": {"choice": '{"value": "stolen"}'},
    "/profiles/{profile_id}/mail/attach": {"mail_account_id": "{mail_account_id}"},
    "/outreach/{outreach_id}/verdict": {"verdict": "bad-fit"},
    "/outreach/{outreach_id}/pitch/write": {"instruction": "shorter", "subject": "", "body": ""},
    "/outreach/{outreach_id}/pitch/save": {"subject": "Stolen", "body": "Stolen draft"},
    "/outreach/{outreach_id}/pitch/send": {"subject": "Stolen", "body": "Stolen draft", "send_key": "walk"},
    "/settings/claude-budget": {"budget": "9.00"},
    "/people/members": {"email": "x@example.com", "artist_id": "new", "new_artist_name": "Stolen"},
    "/people/artists/{artist_id}/rename": {"name": "Stolen"},
}

# Every id the walk fills in, by its key in the ids dict. These are the URL placeholders, except
# `reference_artist_id`, which fills `{artist_id}` on profile routes (there it's a reference
# artist on the profile, while on People routes `{artist_id}` is an Artist).
ID_PARAMS = (
    "profile_id",
    "genre_id",
    "reference_artist_id",
    "signal_id",
    "track_id",
    "term_id",
    "outreach_id",
    "artist_id",
    "user_id",
)
FORM_ARTIST_ID = "form_artist_id"
# Routes whose form carries an artist id: creating a profile, adding a member.
FORM_ARTIST_ID_ROUTES = {("POST", "/profiles"), ("POST", "/people/members")}
NOT_IDS = {"section", "day"}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def registered_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) the app answers, including routes inside included routers.

    FastAPI 0.141 keeps included routers nested inside `app.routes`, so scanning that list for
    APIRoutes finds only `/` and `/health`. `iter_route_contexts` flattens them, and its `path`
    includes any prefix given to `include_router`. Anything the walk can't call (a mounted
    sub-app other than the static files, a plain Starlette route) fails loudly instead of being
    skipped, because a route the walk can't see is a route nobody checked.
    """
    found = set()
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, Mount) and context.path == STATIC_MOUNT and isinstance(route.app, StaticFiles):
            continue  # files only, nothing per artist
        if not isinstance(route, APIRoute):
            # A mount inside an included router can report an empty path; fall back to its own.
            where = context.path or getattr(route, "path", "") or getattr(route, "name", "") or repr(route)
            raise AssertionError(
                f"The route walk can't see inside {type(route).__name__} {where!r}. "
                "Give its routes to the app as APIRoutes, or extend tests/route_walk.py to walk them."
            )
        found.update((method, context.path) for method in context.methods)
    return found


def id_positions(method: str, path: str) -> list[str]:
    """Each id the route takes, as its key in the ids dict, or FORM_ARTIST_ID for the form's artist."""
    positions = []
    for name in PLACEHOLDER.findall(path):
        if name in NOT_IDS:
            continue
        if name == "artist_id" and path.startswith("/profiles/"):
            name = "reference_artist_id"
        positions.append(name)
    if (method, path) in FORM_ARTIST_ID_ROUTES:
        positions.append(FORM_ARTIST_ID)
    return positions


def fill_path(path: str, ids: dict) -> str:
    values = dict(ids)
    if path.startswith("/profiles/"):
        values["artist_id"] = ids["reference_artist_id"]  # on profile routes it's a reference artist
    return path.format(**values)


def fill_form_value(value: str, ids: dict) -> str:
    """A form value that is exactly "{some_id}" is filled from `ids`, like a URL's placeholders.

    Only a whole-value placeholder counts: an Ask Claude choice is JSON, full of braces that
    mean nothing to the walk and must reach the route as typed.
    """
    match = PLACEHOLDER.fullmatch(value)
    return str(ids[match.group(1)]) if match else value


def call(
    client: TestClient,
    method: str,
    path: str,
    ids: dict,
    *,
    token: str,
    htmx: bool = True,
    form_artist_id: object | None = None,
):
    """Call a route from ROUTES the way the page would, with `ids` filled into the URL and form.

    The form's artist id (creating a profile, adding a member) is `ids["artist_id"]` unless
    `form_artist_id` says otherwise.
    """
    form = {key: fill_form_value(value, ids) for key, value in FORMS.get(path, {}).items()}
    if (method, path) in FORM_ARTIST_ID_ROUTES:
        form["artist_id"] = str(ids["artist_id"] if form_artist_id is None else form_artist_id)
    headers = {"accept": "text/html", "x-csrf-token": token}
    if htmx:
        headers["hx-request"] = "true"
    return client.request(method, fill_path(path, ids), data=form or None, headers=headers)
