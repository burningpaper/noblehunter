"""The research agent: Claude with a web search, a page reader and a strict budget.

No test here calls Claude. A scripted client plays Claude's side of the conversation, so each
test can check what the agent does with a tool call, a failing page, a spent budget or a
reply that never gets round to reporting.
"""

import copy
from decimal import Decimal
from itertools import chain, repeat
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from core.llm_costs import cost_of
from pipeline.research import Budget, ClaimedContact, Evidence, Lead
from pipeline.research_agent import DEFAULT_RESEARCH_MODEL, ClaudeResearchAgent, ResearchAgentError
from pipeline.web import FetchError, Page, WebResult

LEAD = Lead(
    playlist_id="1" * 22,
    playlist_name="Glitch Garden",
    playlist_url="https://open.spotify.com/playlist/" + "1" * 22,
    description="Our favourite IDM",
    curator_name="glitchlists",
    owner_spotify_id="glitchlists",
    reference_artists=("Autechre", "Boards of Canada"),
)
REPORT = {
    "person_found": True,
    "service_account": False,
    "summary": "Berlin curator",
    "contacts": [
        {
            "route_type": "instagram",
            "value": "glitchlists",
            "confidence": "B",
            "source_url": "https://www.instagram.com/glitchlists/",
            "note": "Bio says playlist curator",
        }
    ],
}
FORCED_REPORT = {"type": "tool", "name": "report_findings"}


def usage(input_tokens: int = 1000, output_tokens: int = 200) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def tool_call(name: str, arguments: dict, call_id: str = "call-1") -> SimpleNamespace:
    block = SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)
    return SimpleNamespace(content=[block], stop_reason="tool_use", usage=usage())


def words(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage()
    )


class ScriptedMessages:
    """Plays Claude: returns scripted responses in order and keeps a copy of every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("Claude was called more often than scripted")
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakePages:
    def __init__(self, pages: dict | None = None):
        self.pages = pages or {}
        self.requested: list[str] = []

    def fetch(self, url: str) -> Page:
        self.requested.append(url)
        if url not in self.pages:
            raise FetchError("http", "HTTP 404 from that site")
        return self.pages[url]


class FakeSearch:
    def __init__(self, results: list[WebResult] | None = None):
        self.results = results or []
        self.queries: list[str] = []

    def search(self, query: str) -> list[WebResult]:
        self.queries.append(query)
        return self.results


def agent_with(*responses, pages=None, search=None, max_turns: int = 6):
    messages = ScriptedMessages(*responses)
    agent = ClaudeResearchAgent(
        SimpleNamespace(messages=messages),
        pages=pages or FakePages(),
        search=search or FakeSearch(),
        max_turns=max_turns,
    )
    return agent, messages


def last_tool_result(call: dict) -> dict:
    return call["messages"][-1]["content"][0]


class TestInvestigating:
    def test_claude_is_told_about_the_playlist_and_curator(self):
        agent, messages = agent_with(tool_call("report_findings", REPORT))

        agent.investigate(LEAD, Budget())

        call = messages.calls[0]
        prompt = call["messages"][0]["content"]
        for expected in (
            "Glitch Garden",
            "glitchlists",
            "https://open.spotify.com/user/glitchlists",
            "Our favourite IDM",
            "Autechre",
        ):
            assert expected in prompt
        assert call["model"] == DEFAULT_RESEARCH_MODEL
        assert {tool["name"] for tool in call["tools"]} == {"web_search", "fetch_page", "report_findings"}
        assert call["tool_choice"] == {"type": "auto"}

    def test_searches_then_reports_what_it_found(self):
        result = WebResult(
            "glitchlists (@glitchlists) • Instagram",
            "https://www.instagram.com/glitchlists/",
            "Playlist curator.",
        )
        search = FakeSearch([result])
        agent, _ = agent_with(
            tool_call("web_search", {"query": 'site:instagram.com "glitchlists"'}),
            tool_call("report_findings", REPORT, "call-2"),
            search=search,
        )
        budget = Budget()

        report = agent.investigate(LEAD, budget)

        assert search.queries == ['site:instagram.com "glitchlists"']
        assert report.contacts == (
            ClaimedContact(
                "instagram",
                "glitchlists",
                "B",
                "https://www.instagram.com/glitchlists/",
                "Bio says playlist curator",
            ),
        )
        assert report.person_found
        assert not report.service_account
        assert report.summary == "Berlin curator"
        assert (
            Evidence(
                "https://www.instagram.com/glitchlists/",
                "glitchlists (@glitchlists) • Instagram Playlist curator.",
            )
            in report.evidence
        )
        assert budget.fetches == 1
        assert report.spend_usd == Decimal("0.008")

    def test_reads_pages_and_shows_claude_the_contacts_on_them(self):
        about = Page("https://glitchlists.net/about", "About", "Send demos to demos@glitchlists.net", ())
        agent, messages = agent_with(
            tool_call("fetch_page", {"url": "https://glitchlists.net/about"}),
            tool_call("report_findings", {**REPORT, "contacts": []}, "call-2"),
            pages=FakePages({"https://glitchlists.net/about": about}),
        )

        report = agent.investigate(LEAD, Budget())

        sent_back = last_tool_result(messages.calls[1])
        assert sent_back["type"] == "tool_result"
        assert sent_back["tool_use_id"] == "call-1"
        assert "demos@glitchlists.net" in sent_back["content"]
        assert any(
            item.url == "https://glitchlists.net/about" and "demos@glitchlists.net" in item.text
            for item in report.evidence
        )

    def test_a_page_that_cannot_be_read_goes_back_to_claude_as_an_error(self):
        agent, messages = agent_with(
            tool_call("fetch_page", {"url": "https://gone.example"}),
            tool_call("report_findings", REPORT, "call-2"),
        )

        agent.investigate(LEAD, Budget())

        sent_back = last_tool_result(messages.calls[1])
        assert sent_back["is_error"] is True
        assert "404" in sent_back["content"]

    def test_an_unknown_tool_is_refused(self):
        agent, messages = agent_with(
            tool_call("delete_everything", {}), tool_call("report_findings", REPORT, "call-2")
        )

        agent.investigate(LEAD, Budget())

        assert last_tool_result(messages.calls[1])["is_error"] is True

    def test_malformed_contacts_in_the_report_are_skipped(self):
        report = {
            **REPORT,
            "contacts": [
                {"route_type": "email"},
                {"route_type": "email", "value": "a@b.net", "confidence": "Z", "source_url": "https://b.net"},
                {"route_type": "email", "value": "a@b.net", "confidence": "A", "source_url": "https://b.net"},
            ],
        }
        agent, _ = agent_with(tool_call("report_findings", report))

        assert [contact.value for contact in agent.investigate(LEAD, Budget()).contacts] == ["a@b.net"]


class TestStayingInBudget:
    def test_when_the_fetch_budget_is_spent_claude_must_report(self):
        search = FakeSearch()
        agent, messages = agent_with(
            tool_call("web_search", {"query": "one"}),
            tool_call("web_search", {"query": "two"}, "call-2"),
            tool_call("report_findings", REPORT, "call-3"),
            search=search,
        )

        agent.investigate(LEAD, Budget(max_fetches=1))

        assert search.queries == ["one"]
        assert messages.calls[1]["tool_choice"] == FORCED_REPORT
        refused = last_tool_result(messages.calls[2])
        assert refused["is_error"] is True
        assert "budget" in refused["content"].lower()

    def test_out_of_time_means_report_now(self):
        ticks = chain([0.0], repeat(100.0))
        agent, messages = agent_with(tool_call("report_findings", REPORT))

        agent.investigate(LEAD, Budget(max_seconds=60, clock=lambda: next(ticks)))

        assert messages.calls[0]["tool_choice"] == FORCED_REPORT

    def test_the_last_turn_insists_on_a_report(self):
        agent, messages = agent_with(
            words("Let me think."),
            words("Still thinking."),
            tool_call("report_findings", REPORT),
            max_turns=3,
        )

        report = agent.investigate(LEAD, Budget())

        assert messages.calls[2]["tool_choice"] == FORCED_REPORT
        assert report.person_found

    def test_no_report_at_all_is_an_error_not_a_verdict(self):
        agent, _ = agent_with(words("hmm"), words("hmm"), max_turns=2)

        with pytest.raises(ResearchAgentError):
            agent.investigate(LEAD, Budget())

    def test_api_failures_become_a_research_error(self):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        agent, _ = agent_with(anthropic.APIConnectionError(request=request))

        with pytest.raises(ResearchAgentError, match="Claude"):
            agent.investigate(LEAD, Budget())


class TestCosts:
    def test_prices_tokens_by_model(self):
        assert cost_of("claude-sonnet-5", usage(1_000_000, 100_000)) == Decimal("3")
        assert cost_of("claude-haiku-4-5", usage(1_000_000, 0)) == Decimal("1")

    def test_cache_reads_and_writes_are_priced_differently(self):
        cached = SimpleNamespace(
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=1_000_000,
            cache_creation_input_tokens=1_000_000,
        )

        assert cost_of("claude-sonnet-5", cached) == Decimal("0.2") + Decimal("2.5")

    def test_missing_usage_fields_count_as_zero(self):
        partial = SimpleNamespace(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=None)

        assert cost_of("claude-sonnet-5", partial) == Decimal("2")

    def test_an_unknown_model_is_priced_as_the_most_expensive(self):
        assert cost_of("claude-mystery", usage(1_000_000, 0)) == Decimal("5")
