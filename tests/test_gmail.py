"""The Gmail client: refresh a token, send, and read messages, with failures sorted into kinds."""

import json

import httpx
import pytest

from core.gmail import ACCESS_TOKEN_URL, API_ROOT, Gmail, GmailError

REFRESH = "1//0refresh"
CLIENT = {"client_id": "id.apps.googleusercontent.com", "client_secret": "secret"}


def gmail(handler, refresh_token: str = REFRESH) -> Gmail:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Gmail(client, refresh_token=refresh_token, **CLIENT)


def token_response(expires_in: int = 3600) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "ya29.access", "expires_in": expires_in})


def failing_at(status: int, payload: dict | None = None, text: str | None = None):
    """A handler that hands out a token, then answers every API call with the same failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=payload or {})

    return handler


def test_it_refreshes_once_and_reuses_the_access_token():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        assert request.headers["authorization"] == "Bearer ya29.access"
        return httpx.Response(200, json={"emailAddress": "synman@gmail.com", "historyId": "9001"})

    api = gmail(handler)
    first = api.profile()
    second = api.profile()

    assert first == second == ("synman@gmail.com", "9001")
    assert calls.count(ACCESS_TOKEN_URL) == 1


def test_a_refused_refresh_token_is_an_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(GmailError) as error:
        gmail(handler).profile()

    assert error.value.kind == "auth"
    assert REFRESH not in str(error.value)


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "transient"), (503, "transient"), (400, "rejected")],
)
def test_api_failures_are_sorted_into_kinds(status, kind):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(GmailError) as error:
        gmail(handler).profile()

    assert error.value.kind == kind


@pytest.mark.parametrize(
    "body",
    [
        {
            "error": {
                "code": 403,
                "errors": [{"reason": "userRateLimitExceeded"}],
                "status": "RESOURCE_EXHAUSTED",
            }
        },
        {"error": {"code": 403, "errors": [{"reason": "rateLimitExceeded"}]}},
        {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}},
    ],
)
def test_a_rate_limit_dressed_as_403_is_transient_not_auth(body):
    # Gmail answers 403 for "slow down" as well as "no". Calling this `auth` would flag the
    # mailbox as needing reconnection, and nothing clears that but reconnecting through Google.
    # Checked before the configuration reasons, so a busy mailbox never reads as a broken project.
    with pytest.raises(GmailError) as error:
        gmail(failing_at(403, body)).profile()

    assert error.value.kind == "transient"


@pytest.mark.parametrize(
    "body",
    [
        {
            "error": {
                "code": 403,
                "errors": [{"reason": "accessNotConfigured"}],
                "status": "PERMISSION_DENIED",
            }
        },
        {"error": {"code": 403, "status": "SERVICE_DISABLED"}},
    ],
)
def test_an_api_that_was_never_switched_on_is_a_config_error(body):
    # The first real mailbox failed exactly this way: the Gmail API had never been enabled for
    # the Google project, and the app answered "reconnect the mailbox" -- which cannot help.
    with pytest.raises(GmailError) as error:
        gmail(failing_at(403, body)).profile()

    assert error.value.kind == "config"
    assert "Gmail API" in str(error.value)
    assert "enabled" in str(error.value)


def test_a_token_missing_a_scope_is_a_config_error_that_names_the_scope():
    body = {
        "error": {
            "code": 403,
            "errors": [{"reason": "insufficientPermissions"}],
            "status": "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
        }
    }

    with pytest.raises(GmailError) as error:
        gmail(failing_at(403, body)).profile()

    assert error.value.kind == "config"
    assert "scope" in str(error.value)


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": 403, "errors": [{"reason": "forbidden"}], "status": "PERMISSION_DENIED"}},
        {"error": {"message": "nope"}},
        {},
    ],
)
def test_a_403_naming_no_reason_we_know_is_still_auth(body):
    with pytest.raises(GmailError) as error:
        gmail(failing_at(403, body)).profile()

    assert error.value.kind == "auth"


def test_a_403_with_no_readable_body_is_still_auth():
    with pytest.raises(GmailError) as error:
        gmail(failing_at(403, text="<html>a proxy said no</html>")).profile()

    assert error.value.kind == "auth"


def test_a_network_failure_is_transient_and_names_no_token():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        raise httpx.ConnectError("boom")

    with pytest.raises(GmailError) as error:
        gmail(handler).profile()

    assert error.value.kind == "transient"
    assert "ya29" not in str(error.value)


def test_send_posts_the_raw_message_and_returns_its_ids():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        sent["path"] = request.url.path
        sent["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"id": "m1", "threadId": "t1"})

    result = gmail(handler).send("cmF3", thread_id="t1")

    assert result == ("m1", "t1")
    assert sent["path"] == "/gmail/v1/users/me/messages/send"
    assert sent["body"] == {"raw": "cmF3", "threadId": "t1"}


def test_a_first_pitch_is_sent_without_a_thread():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        sent["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"id": "m1", "threadId": "t1"})

    gmail(handler).send("cmF3")

    assert sent["body"] == {"raw": "cmF3"}


def test_history_returns_added_message_ids_and_the_new_history_id():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(
            200,
            json={
                "historyId": "9100",
                "history": [
                    {"messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}]},
                    {"messagesAdded": [{"message": {"id": "m2", "threadId": "t2"}}]},
                    {"labelsAdded": [{"message": {"id": "ignored", "threadId": "t9"}}]},
                ],
            },
        )

    added, history_id = gmail(handler).history_since("9000")

    assert added == [("m1", "t1"), ("m2", "t2")]
    assert history_id == "9100"


def test_an_expired_history_id_is_its_own_kind():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(404, json={"error": {"message": "Requested entity was not found."}})

    with pytest.raises(GmailError) as error:
        gmail(handler).history_since("1")

    assert error.value.kind == "history-gone"


def test_an_answer_that_is_not_json_is_rejected_rather_than_crashing():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(200, text="<html>a captive portal</html>")

    with pytest.raises(GmailError) as error:
        gmail(handler).profile()

    assert error.value.kind == "rejected"


@pytest.mark.parametrize("payload", [{"historyId": "9001"}, {"emailAddress": "", "historyId": "9001"}])
def test_a_profile_that_names_no_address_is_rejected_rather_than_empty(payload):
    # An empty address used to come straight back from here and reach `connect_mailbox`, which
    # would store a mailbox with no address at all. Every later send from it then built an empty
    # From header and failed far away from the cause.
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(200, json=payload)

    with pytest.raises(GmailError) as error:
        gmail(handler).profile()

    assert error.value.kind == "rejected"


def test_a_send_that_names_no_message_is_rejected_rather_than_crashing():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(200, json={"nothing": "useful"})

    with pytest.raises(GmailError) as error:
        gmail(handler).send("cmF3")

    assert error.value.kind == "rejected"


def test_a_refused_access_token_is_not_presented_again():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        return httpx.Response(401, json={"error": {"message": "nope"}})

    api = gmail(handler)
    for _ in range(2):
        with pytest.raises(GmailError):
            api.profile()

    # Two refreshes, not one: the refused token was dropped rather than offered again.
    assert calls.count(ACCESS_TOKEN_URL) == 2


def test_message_and_thread_ask_for_full_format():
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ACCESS_TOKEN_URL:
            return token_response()
        asked.append(str(request.url))
        return httpx.Response(200, json={"id": "m1", "threadId": "t1", "messages": []})

    api = gmail(handler)
    api.message("m1")
    api.thread("t1")

    assert f"{API_ROOT}/messages/m1?format=full" in asked[0]
    assert f"{API_ROOT}/threads/t1?format=full" in asked[1]
