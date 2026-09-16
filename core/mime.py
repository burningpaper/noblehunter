"""The message we send, and the message Gmail gives back.

Plain text only, which is what a curator's inbox actually wants and what keeps this small: no
attachments, no HTML, no tracking pixel. Header fields are checked for line breaks, because a
newline in an address or subject would otherwise let someone add their own headers (a Bcc, say)
from a form field.

Reading is the other direction: Gmail hands back a nested payload, and callers want the sender,
the time, and the part a person actually wrote, with the quoted history below it split off so
a thread doesn't repeat itself on screen.
"""

import base64
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser

# "On <date> X wrote:", the line Gmail and most clients put above quoted history.
QUOTE_START = re.compile(r"^(On .{5,120}wrote:|-{2,} ?Original Message ?-{2,}|_{10,})\s*$", re.MULTILINE)


class HeaderProblem(ValueError):
    """A header field that can't be used as given (a line break, or no address at all)."""


@dataclass(frozen=True)
class ParsedMessage:
    gmail_message_id: str
    gmail_thread_id: str
    from_address: str
    to_address: str
    subject: str
    body_text: str
    quoted_text: str | None
    sent_at: datetime
    message_id_header: str | None


def build_message(
    *,
    from_address: str,
    to_address: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: tuple[str, ...] = (),
) -> str:
    """A plain-text message as base64url, ready for Gmail's `raw` field."""
    message = EmailMessage()
    message["From"] = _header("From", from_address)
    message["To"] = _header("To", to_address)
    message["Subject"] = _header("Subject", subject)
    if in_reply_to:
        message["In-Reply-To"] = _header("In-Reply-To", in_reply_to)
    if references:
        message["References"] = _header("References", " ".join(references))
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def parse_message(payload: dict) -> ParsedMessage:
    """One Gmail message (format=full) as the fields Noble Hunter stores."""
    headers = {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in payload.get("payload", {}).get("headers", [])
    }
    body, is_html = _first_body(payload.get("payload", {}))
    text = _as_text(body, is_html)
    written, quoted = _split_quoted(text)
    sent_ms = int(payload.get("internalDate") or 0)
    return ParsedMessage(
        gmail_message_id=str(payload.get("id", "")),
        gmail_thread_id=str(payload.get("threadId", "")),
        from_address=parseaddr(headers.get("from", ""))[1],
        to_address=parseaddr(headers.get("to", ""))[1],
        subject=headers.get("subject", ""),
        body_text=written,
        quoted_text=quoted,
        sent_at=datetime.fromtimestamp(sent_ms / 1000, tz=UTC),
        message_id_header=headers.get("message-id") or None,
    )


def _header(name: str, value: str) -> str:
    clean = value.strip()
    if not clean:
        raise HeaderProblem(f"{name} is empty")
    if "\n" in clean or "\r" in clean:
        raise HeaderProblem(f"{name} can't contain a line break")
    return clean


def _first_body(part: dict) -> tuple[str, bool]:
    """The best body in a Gmail payload: plain text if there is any, otherwise HTML."""
    plain = _find(part, "text/plain")
    if plain is not None:
        return plain, False
    html = _find(part, "text/html")
    return (html, True) if html is not None else ("", False)


def _find(part: dict, mime_type: str) -> str | None:
    if part.get("mimeType") == mime_type:
        data = part.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")
    for child in part.get("parts", []):
        found = _find(child, mime_type)
        if found is not None:
            return found
    return None


def _as_text(body: str, is_html: bool) -> str:
    return _strip_html(body) if is_html else body.replace("\r\n", "\n").strip()


def _split_quoted(text: str) -> tuple[str, str | None]:
    match = QUOTE_START.search(text)
    if match is None:
        return text.strip(), None
    return text[: match.start()].strip(), text[match.start() :].strip() or None


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"p", "br", "div", "tr"}:
            self.parts.append("\n")


def _strip_html(html: str) -> str:
    parser = _TextOnly()
    parser.feed(html)
    lines = [line.strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()
