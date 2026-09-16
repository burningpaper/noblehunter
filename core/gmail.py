"""Just enough of the Gmail API to pitch and to read replies.

Both halves of Noble Hunter use this: the web app sends, and the runner reads replies, so it
lives in `core/` and depends only on httpx. It holds a refresh token (decrypted by the caller),
swaps it for a short-lived access token, and keeps that in memory until it expires.

Failures come back as `GmailError` with a `kind` the caller acts on:

- `auth`: Google refused the token. The mailbox needs reconnecting; nothing will fix itself.
- `config`: the app's own Google Cloud project is wrong -- the Gmail API switched off, or a token
  issued without a scope the call needs. No mailbox is broken, so reconnecting one changes nothing.
- `transient`: rate limit, server error or network. Worth trying again later.
- `rejected`: Gmail understood and said no (a malformed message, a missing thread).
- `history-gone`: the mailbox's history id is too old, so the caller re-reads whole threads.

Nothing here logs a token, an address or a message body.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

ACCESS_TOKEN_URL = "https://oauth2.googleapis.com/token"
API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT_SECONDS = 30
TOKEN_EARLY_REFRESH_SECONDS = 60  # refresh a little before Google's expiry, to avoid a race
# The reasons Google gives when a 403 means "too many requests", not "you may not".
RATE_LIMIT_REASONS = frozenset(
    {"ratelimitexceeded", "userratelimitexceeded", "quotaexceeded", "resource_exhausted"}
)
# The reasons Google gives when a 403 is about the app's Google Cloud project rather than this
# mailbox: the API never switched on, or a token issued without the scope the call needs.
API_DISABLED_REASONS = frozenset({"accessnotconfigured", "service_disabled", "servicedisabled"})
SCOPE_REASONS = frozenset({"insufficientpermissions", "access_token_scope_insufficient"})
CONFIG_REASONS = API_DISABLED_REASONS | SCOPE_REASONS


class GmailError(RuntimeError):
    """Gmail couldn't do it. `kind` is auth, config, transient, rejected or history-gone."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class _AccessToken:
    value: str
    expires_at: float


class Gmail:
    """One mailbox's API access. Build one per mailbox; it caches its access token in memory."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        now: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._now = now
        self._token: _AccessToken | None = None

    # --- the calls Noble Hunter makes ----------------------------------------------------

    def profile(self) -> tuple[str, str]:
        """The mailbox's own address and its current history id."""
        data = self._get(f"{API_ROOT}/profile")
        address = str(data.get("emailAddress", "")).strip()
        if not address:
            # An empty address would be stored as a mailbox with no address, and every send from
            # it would then fail on an empty From header, far away from this cause.
            raise GmailError("rejected", "Gmail's profile didn't say which mailbox it is")
        return address, str(data.get("historyId", ""))

    def send(self, raw: str, *, thread_id: str | None = None) -> tuple[str, str]:
        """Send a base64url MIME message. Returns (message id, thread id)."""
        body: dict = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        data = self._post(f"{API_ROOT}/messages/send", body)
        try:
            return str(data["id"]), str(data["threadId"])
        except KeyError:
            # Callers switch on `kind`; a surprise shape must not escape as a bare KeyError.
            raise GmailError("rejected", "Gmail took the message but didn't say which one") from None

    def history_since(self, history_id: str) -> tuple[list[tuple[str, str]], str]:
        """Messages added since `history_id`, as (message id, thread id), and the new history id."""
        data = self._get(
            f"{API_ROOT}/history", params={"startHistoryId": history_id, "historyTypes": "messageAdded"}
        )
        added = [
            (str(item["message"]["id"]), str(item["message"]["threadId"]))
            for record in data.get("history", [])
            for item in record.get("messagesAdded", [])
        ]
        return added, str(data.get("historyId", history_id))

    def message(self, message_id: str) -> dict:
        return self._get(f"{API_ROOT}/messages/{message_id}", params={"format": "full"})

    def thread(self, thread_id: str) -> dict:
        return self._get(f"{API_ROOT}/threads/{thread_id}", params={"format": "full"})

    # --- plumbing -------------------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token is not None and self._now() < self._token.expires_at:
            return self._token.value
        try:
            response = self._client.post(
                ACCESS_TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise GmailError("transient", f"Couldn't reach Google ({type(error).__name__})") from None
        if response.status_code != 200:
            # A refused refresh token never fixes itself: the mailbox has to be reconnected.
            raise GmailError("auth", "Google refused the saved Gmail access; reconnect the mailbox")
        payload = response.json()
        expires_in = int(payload.get("expires_in", 3600)) - TOKEN_EARLY_REFRESH_SECONDS
        self._token = _AccessToken(str(payload["access_token"]), self._now() + max(expires_in, 0))
        return self._token.value

    def _get(self, url: str, params: dict | None = None) -> dict:
        return self._call("GET", url, params=params)

    def _post(self, url: str, body: dict) -> dict:
        return self._call("POST", url, json=body)

    def _call(self, method: str, url: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        try:
            response = self._client.request(method, url, headers=headers, timeout=TIMEOUT_SECONDS, **kwargs)
        except httpx.HTTPError as error:
            raise GmailError("transient", f"Couldn't reach Gmail ({type(error).__name__})") from None
        if response.status_code < 400:
            return _payload(response, url)
        kind = _kind(response, url)
        if kind == "auth":
            # Google has refused the token we hold; don't keep presenting it until it expires.
            self._token = None
        if kind == "config":
            # "Gmail said 403 to profile" is true and useless: it sent the first person to
            # reconnect a mailbox that was never broken. Name the setting that is wrong instead.
            raise GmailError(kind, _config_message(response))
        raise GmailError(kind, f"Gmail said {response.status_code} to {_what(url)}")


def _payload(response: httpx.Response, url: str) -> dict:
    """Gmail's answer as a dict, or a `rejected` error -- never a raw ValueError past the kinds."""
    try:
        data = response.json()
    except ValueError:
        raise GmailError("rejected", f"Gmail's answer about {_what(url)} wasn't JSON") from None
    if not isinstance(data, dict):
        raise GmailError("rejected", f"Gmail's answer about {_what(url)} wasn't a message")
    return data


def _kind(response: httpx.Response, url: str) -> str:
    if response.status_code == 403 and _is_rate_limit(response):
        # Gmail says 403 for "too many requests" as well as for "no", and the two need opposite
        # handling: this one clears by itself, while `auth` flags the mailbox as needing a person
        # to reconnect it -- and nothing clears that flag but reconnecting through Google.
        return "transient"
    if response.status_code == 403 and _is_config(response):
        # Not the token: the app's own Google project. Reconnecting a mailbox cannot switch an API
        # on or widen a scope, and calling this `auth` both demands exactly that and leaves
        # `needs_reconnect` -- which only a completed consent flow clears -- on a working mailbox.
        return "config"
    if response.status_code in (401, 403):
        return "auth"
    if response.status_code == 404 and "/history" in url:
        return "history-gone"  # Gmail drops history older than about a week
    if response.status_code == 429 or response.status_code >= 500:
        return "transient"
    return "rejected"


def _reasons(response: httpx.Response) -> set[str]:
    """Every reason Google named in an error body, lowercased.

    Google says the same thing in two places -- `error.errors[].reason` and `error.status` -- and
    which one arrives varies by API, so read both. A body that can't be read names no reason at
    all, which is the honest answer: an unrecognised 403 stays `auth`.
    """
    try:
        payload = response.json()
    except ValueError:
        return set()
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return set()
    listed = error.get("errors", []) or []
    reasons = {str(item.get("reason", "")).lower() for item in listed if isinstance(item, dict)}
    reasons.add(str(error.get("status", "")).lower())
    return reasons


def _is_rate_limit(response: httpx.Response) -> bool:
    """Whether a 403 is Google saying "slow down" rather than "no"."""
    return bool(_reasons(response) & RATE_LIMIT_REASONS)


def _is_config(response: httpx.Response) -> bool:
    """Whether a 403 is about how the app's Google project is set up, not this mailbox's token."""
    return bool(_reasons(response) & CONFIG_REASONS)


def _config_message(response: httpx.Response) -> str:
    """What is actually wrong and where to fix it -- neither of these is fixed by reconnecting."""
    if _reasons(response) & SCOPE_REASONS:
        return (
            "Gmail refused the call: this app's Gmail access is missing a scope. Check the scopes "
            "on the Google Cloud project, then connect the mailbox again to pick up the new ones."
        )
    return (
        "Gmail refused the call: the Gmail API isn't enabled for this app's Google Cloud project. "
        "Switch it on there -- reconnecting the mailbox won't help."
    )


def _what(url: str) -> str:
    """The call being made, with no ids or addresses in it."""
    return url.removeprefix(API_ROOT).split("?")[0].strip("/").split("/")[0] or "the mailbox"
