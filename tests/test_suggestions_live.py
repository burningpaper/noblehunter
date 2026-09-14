"""The real Ask Claude call, end to end. Costs about a cent. Run with `pytest --run-live`."""

import os
import time

import pytest
from dotenv import dotenv_values

from core.suggestions import ClaudeSuggester, ProfileContext

pytestmark = pytest.mark.live


def test_claude_suggests_search_terms_for_an_idm_profile():
    key = os.environ.get("ANTHROPIC_API_KEY") or dotenv_values(".env.local").get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    context = ProfileContext(
        name="Synman",
        genres=("IDM",),
        reference_artists=("Autechre", "Boards of Canada", "Aphex Twin"),
        anti_artists=(),
        anti_terms=("EDM",),
        search_terms=(),
    )

    started = time.monotonic()
    answer = ClaudeSuggester.from_api_key(key).suggest(
        "terms", "What search terms would find IDM playlists on Spotify?", context
    )
    print(f"\n{len(answer)} suggestions in {time.monotonic() - started:.1f}s:")
    for suggestion in answer:
        print(f"  - {suggestion.value}: {suggestion.reason}")

    assert len(answer) >= 3
    assert all(suggestion.value and suggestion.reason for suggestion in answer)
