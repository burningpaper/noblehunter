"""Building the message Gmail sends, and reading back what Gmail returns."""

import base64
from datetime import UTC, datetime
from email import message_from_bytes, policy

import pytest

from core.mime import HeaderProblem, build_message, parse_message


def decoded(raw: str) -> str:
    return base64.urlsafe_b64decode(raw.encode()).decode()


def gmail_payload(
    *,
    body: str = "Hi there",
    subject: str = "A track for Glitch Garden",
    sender: str = "Synman <synman@gmail.com>",
    to: str = "curator@example.com",
    message_id: str = "m1",
    thread_id: str = "t1",
    internal_ms: int = 1_789_000_000_000,
    html_only: bool = False,
) -> dict:
    part = {
        "mimeType": "text/html" if html_only else "text/plain",
        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
    }
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": str(internal_ms),
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": "<abc@mail.gmail.com>"},
            ],
            "mimeType": "multipart/alternative",
            "parts": [part],
        },
    }


class TestBuilding:
    def test_a_first_pitch_has_the_fields_gmail_needs(self):
        raw = build_message(
            from_address="synman@gmail.com",
            to_address="curator@example.com",
            subject="A track for Glitch Garden",
            body="Hi there\n\nSynman",
        )

        text = decoded(raw)
        assert "To: curator@example.com" in text
        assert "Subject: A track for Glitch Garden" in text
        assert text.rstrip().endswith("Synman")
        assert "Content-Type: text/plain" in text

    def test_a_reply_threads_onto_the_last_message(self):
        raw = build_message(
            from_address="synman@gmail.com",
            to_address="curator@example.com",
            subject="Re: A track for Glitch Garden",
            body="Thanks!",
            in_reply_to="<abc@mail.gmail.com>",
            references=("<first@mail.gmail.com>", "<abc@mail.gmail.com>"),
        )

        text = decoded(raw)
        assert "In-Reply-To: <abc@mail.gmail.com>" in text
        assert "References: <first@mail.gmail.com> <abc@mail.gmail.com>" in text

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("to_address", "curator@example.com\nBcc: sneak@example.com"),
            ("to_address", "curator@example.com\r\nBcc: sneak@example.com"),
            ("subject", "Hello\nBcc: sneak@example.com"),
        ],
    )
    def test_a_header_cannot_carry_a_second_line(self, field, value):
        fields = {
            "from_address": "synman@gmail.com",
            "to_address": "curator@example.com",
            "subject": "Hello",
            "body": "Hi",
            field: value,
        }

        with pytest.raises(HeaderProblem):
            build_message(**fields)

    def test_the_body_may_contain_anything(self):
        raw = build_message(
            from_address="synman@gmail.com",
            to_address="curator@example.com",
            subject="Hello",
            body="Line one\nBcc: this is just text\n\n-- \nSynman",
        )

        assert "Bcc: this is just text" in decoded(raw)


class TestReading:
    def test_it_reads_the_plain_text_part(self):
        parsed = parse_message(gmail_payload(body="Hi there\n\nSynman"))

        assert parsed.gmail_message_id == "m1"
        assert parsed.gmail_thread_id == "t1"
        assert parsed.from_address == "synman@gmail.com"
        assert parsed.to_address == "curator@example.com"
        assert parsed.subject == "A track for Glitch Garden"
        assert parsed.body_text == "Hi there\n\nSynman"
        assert parsed.quoted_text is None
        assert parsed.sent_at == datetime(2026, 9, 10, 0, 26, 40, tzinfo=UTC)
        assert parsed.message_id_header == "<abc@mail.gmail.com>"

    def test_quoted_history_is_split_off(self):
        body = (
            "Yes please, send it over.\n\n"
            "On Tue, 15 Sep 2026 at 09:12, Synman <synman@gmail.com> wrote:\n"
            "> Hi there, I have a track\n"
        )

        parsed = parse_message(gmail_payload(body=body))

        assert parsed.body_text == "Yes please, send it over."
        assert parsed.quoted_text.startswith("On Tue, 15 Sep 2026")

    def test_an_html_only_message_becomes_readable_text(self):
        html = "<p>Hi <b>there</b></p><p>Send it</p>"

        parsed = parse_message(gmail_payload(body=html, html_only=True))

        assert "Hi there" in parsed.body_text
        assert "<p>" not in parsed.body_text

    def test_a_display_name_is_kept_out_of_the_address(self):
        parsed = parse_message(gmail_payload(sender="Nik Davies <nik@valleyview.example>"))

        assert parsed.from_address == "nik@valleyview.example"

    def test_a_message_with_no_body_parses_as_empty(self):
        payload = gmail_payload()
        payload["payload"] = {"headers": payload["payload"]["headers"], "mimeType": "text/plain", "body": {}}

        parsed = parse_message(payload)

        assert parsed.body_text == ""

    def test_a_message_we_built_reads_back_the_same(self):
        # Gmail hands a sent message back through sync, so what we build must survive the trip.
        body = "Hi there\n\nThe track is called Glass Weather.\n\nSynman"
        raw = build_message(
            from_address="synman@gmail.com",
            to_address="curator@example.com",
            subject="A track for Glitch Garden",
            body=body,
        )
        sent = message_from_bytes(base64.urlsafe_b64decode(raw.encode()), policy=policy.default)

        parsed = parse_message(gmail_payload(body=sent.get_content(), subject=sent["Subject"]))

        assert parsed.body_text == body
        assert parsed.subject == "A track for Glitch Garden"
