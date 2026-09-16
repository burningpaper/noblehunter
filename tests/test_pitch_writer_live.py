"""One real call to Claude, to read what it actually writes. Costs about a cent.

Run with `pytest tests/test_pitch_writer_live.py --run-live -s`, and read the draft: the prompt
is the product here, and no fake client can tell you whether a curator would answer it.
"""

import os

import pytest
from dotenv import dotenv_values

from core.pitch_writer import ClaudePitchWriter
from tests.test_pitch_writer import REQUEST

pytestmark = pytest.mark.live


def test_claude_writes_something_a_person_would_send():
    key = os.environ.get("ANTHROPIC_API_KEY") or dotenv_values(".env.local").get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY is not set")

    pitch = ClaudePitchWriter.from_api_key(key).write(REQUEST)

    print(f"\nSubject: {pitch.subject}\n\n{pitch.body}\n\n(${pitch.spend_usd:.4f})")
    assert "Broken Machines" in pitch.body
    assert len(pitch.body.split()) < 200
