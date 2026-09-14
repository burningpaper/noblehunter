"""Contact research: find the person behind a qualified playlist.

A pitch needs a human. For each playlist likely to reach the digest, research climbs the spec's
ladder and stops at the first confident answer:

1. The playlist's own description. An email or handle the curator wrote there is grade A:
   they chose it.
2. The links in that description (a Linktree, their site, a label page), within the playlist's
   fetch budget. Contacts found there are grade A too, sourced to the page they appear on.
3. Only then Claude, which can search the web and read pages to find the person or their
   profiles.

Claude's answer is never taken on trust. Each contact it reports is checked against the text
the tools actually returned. To keep grade A, a contact must appear on the page it cites; for
grade B, somewhere the agent really looked. Anything else is recorded as C, a guess that never
reaches the digest.

The nightly loop spends the budget where it counts. It takes qualified playlists best fit
first, one per curator. It stops for a profile once that profile has enough reachable curators,
and never spends past the night's Claude budget. When the money runs out, a playlist that
needed Claude is left for another night rather than written off as unreachable.
"""

import logging
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.contacts import contact_key, email_domain_key, normalize_email, normalize_handle, normalize_url
from core.exclusion import contact_is_excluded, curator_is_eligible
from core.models import (
    Confidence,
    Contact,
    Curator,
    Playlist,
    PlaylistProfileFit,
    PlaylistStatus,
    Profile,
    Run,
)
from pipeline.contact_extract import extract_contacts
from pipeline.runs import record_stage
from pipeline.web import FetchError, Page

logger = logging.getLogger("noble_hunter.research")

PLAYLIST_URL = "https://open.spotify.com/playlist/{}"
MAX_FETCHES_PER_PLAYLIST = 10
MAX_SECONDS_PER_PLAYLIST = 60
MAX_LINKS_FOLLOWED = 3
MAX_ERROR_LENGTH = 300
STRONG = frozenset({Confidence.A, Confidence.B})
GRADE_ORDER = {Confidence.A: 0, Confidence.B: 1, Confidence.C: 2}
HANDLE_ROUTES = frozenset({"instagram", "x", "bluesky"})
URL_ROUTES = frozenset({"submission-form", "other"})


# --- What research works with -------------------------------------------------------------


@dataclass
class Budget:
    """One playlist's allowance: page fetches and seconds (the spec's 10 fetches and 60 s)."""

    max_fetches: int = MAX_FETCHES_PER_PLAYLIST
    max_seconds: float = MAX_SECONDS_PER_PLAYLIST
    clock: Callable[[], float] = time.monotonic
    fetches: int = 0
    started_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    def can_fetch(self) -> bool:
        return self.fetches < self.max_fetches and self.clock() - self.started_at < self.max_seconds

    def spend_fetch(self) -> None:
        self.fetches += 1


@dataclass(frozen=True)
class Lead:
    playlist_id: str
    playlist_name: str
    playlist_url: str
    description: str
    curator_name: str
    owner_spotify_id: str | None
    reference_artists: tuple[str, ...] = ()


@dataclass(frozen=True)
class Evidence:
    """Something research actually saw: a page's text, or a search result's title and snippet."""

    url: str
    text: str


@dataclass(frozen=True)
class ClaimedContact:
    route_type: str
    value: str
    confidence: str
    source_url: str
    note: str = ""


@dataclass(frozen=True)
class ContactFinding:
    """A contact after checking: its grade reflects the evidence, not the claim."""

    route_type: str
    value: str
    confidence: str
    source_url: str
    note: str = ""


@dataclass(frozen=True)
class AgentReport:
    contacts: tuple[ClaimedContact, ...] = ()
    person_found: bool = False
    service_account: bool = False
    summary: str = ""
    evidence: tuple[Evidence, ...] = ()
    spend_usd: Decimal = Decimal(0)


class ResearchAgent(Protocol):
    def investigate(self, lead: Lead, budget: Budget) -> AgentReport: ...


class PageSource(Protocol):
    def fetch(self, url: str) -> Page: ...


@dataclass(frozen=True)
class ResearchResult:
    contacts: tuple[ContactFinding, ...] = ()
    service_account: bool = False
    summary: str = ""
    fetches: int = 0
    spend_usd: Decimal = Decimal(0)
    used_agent: bool = False

    @property
    def reachable(self) -> bool:
        return not self.service_account and any(contact.confidence in STRONG for contact in self.contacts)

    @property
    def deferred(self) -> bool:
        """Nothing free turned up and Claude wasn't asked: not a verdict, just not finished."""
        return not self.used_agent and not self.reachable


@dataclass
class ResearchSummary:
    researched: int = 0
    reachable: int = 0
    no_contact: int = 0
    deferred: int = 0
    already_reachable: int = 0
    spend_usd: Decimal = Decimal(0)
    budget_reached: bool = False
    errors: list[str] = field(default_factory=list)


# --- One playlist -------------------------------------------------------------------------


def research_lead(
    lead: Lead, *, pages: PageSource, agent: ResearchAgent | None, budget: Budget
) -> ResearchResult:
    findings = _from_text(lead.description, lead.playlist_url, "In the playlist description")
    if not _any_strong(findings):
        findings += _from_description_links(lead, pages, budget)
    if _any_strong(findings) or agent is None:
        return ResearchResult(contacts=_best_per_contact(findings), fetches=budget.fetches)

    report = agent.investigate(lead, budget)
    findings += verify_claims(report.contacts, report.evidence)
    return ResearchResult(
        contacts=_best_per_contact(findings),
        service_account=report.service_account,
        summary=report.summary,
        fetches=budget.fetches,
        spend_usd=report.spend_usd,
        used_agent=True,
    )


def page_text(page: Page) -> str:
    """What a page offers for contact extraction: its words plus every link target."""
    return " ".join([page.title, page.text, *page.links])


def verify_claims(claims: Iterable[ClaimedContact], evidence: Sequence[Evidence]) -> list[ContactFinding]:
    """Grade each claimed contact by what was actually seen, and drop ones that aren't contacts."""
    findings = []
    for claim in claims:
        value = _normalized_value(claim.route_type, claim.value)
        if value is None:
            continue
        on_source = any(
            _same_page(item.url, claim.source_url) and _mentions(item.text, claim.route_type, value)
            for item in evidence
        )
        anywhere = on_source or any(_mentions(item.text, claim.route_type, value) for item in evidence)
        grade = _grade(claim.confidence, on_source=on_source, anywhere=anywhere)
        findings.append(ContactFinding(claim.route_type, value, grade, claim.source_url, claim.note))
    return findings


def _from_text(text: str, source_url: str, note: str) -> list[ContactFinding]:
    return [
        ContactFinding(route.route_type, route.value, Confidence.A, source_url, note)
        for route in extract_contacts(text).routes
    ]


def _from_description_links(lead: Lead, pages: PageSource, budget: Budget) -> list[ContactFinding]:
    findings: list[ContactFinding] = []
    for link in extract_contacts(lead.description).links[:MAX_LINKS_FOLLOWED]:
        if not budget.can_fetch():
            break
        budget.spend_fetch()
        try:
            page = pages.fetch(link)
        except FetchError as error:
            logger.info("Couldn't read a link from %s's description: %s", lead.playlist_id, error)
            continue
        note = f"On {_host(page.url)}, linked from the playlist description"
        findings += _from_text(page_text(page), page.url, note)
        if _any_strong(findings):
            break
    return findings


def _grade(claimed: str, *, on_source: bool, anywhere: bool) -> str:
    if claimed == Confidence.A and on_source:
        return Confidence.A
    if claimed in STRONG and anywhere:
        return Confidence.B
    return Confidence.C


def _normalized_value(route_type: str, value: str) -> str | None:
    if route_type == "email":
        return normalize_email(value)
    if route_type in HANDLE_ROUTES:
        return normalize_handle(route_type, value)
    if route_type in URL_ROUTES:
        url = value.strip()
        return url if normalize_url(url) else None
    return None


def _mentions(text: str, route_type: str, value: str) -> bool:
    if contact_key(route_type, value) in {route.key for route in extract_contacts(text).routes}:
        return True
    lowered = text.casefold()
    if route_type == "email":
        return value in lowered
    if route_type in HANDLE_ROUTES:
        # "@name" or ".../name", never "name" inside an ordinary word.
        return re.search(rf"[@/]{re.escape(value)}(?![\w.])", lowered) is not None
    normalized = normalize_url(value)
    return bool(normalized) and normalized in lowered


def _same_page(first: str, second: str) -> bool:
    first_key, second_key = normalize_url(first), normalize_url(second)
    return first_key is not None and first_key == second_key


def _any_strong(findings: Iterable[ContactFinding]) -> bool:
    return any(finding.confidence in STRONG for finding in findings)


def _best_per_contact(findings: Iterable[ContactFinding]) -> tuple[ContactFinding, ...]:
    best: dict[str, ContactFinding] = {}
    for finding in findings:
        key = contact_key(finding.route_type, finding.value)
        if key is None:
            continue
        if key not in best or GRADE_ORDER[finding.confidence] < GRADE_ORDER[best[key].confidence]:
            best[key] = finding
    return tuple(best.values())


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


# --- Recording ----------------------------------------------------------------------------


def record_research(
    session: Session, playlist: Playlist, result: ResearchResult, *, today: date, now: datetime
) -> bool:
    """Store what was found. True if the curator can now be reached (a stored A or B contact)."""
    if result.deferred:
        return False

    curator = playlist.curator
    strongest = None
    if curator is not None:
        for finding in result.contacts:
            stored = _save_contact(session, curator, playlist, finding, today)
            if stored is not None and (strongest is None or GRADE_ORDER[stored] < GRADE_ORDER[strongest]):
                strongest = stored

    reachable = strongest in STRONG and not result.service_account
    playlist.last_checked_at = now
    if not reachable:
        playlist.status = PlaylistStatus.NO_CONTACT
    session.flush()
    return reachable


def _save_contact(
    session: Session, curator: Curator, playlist: Playlist, finding: ContactFinding, today: date
) -> str | None:
    """The grade now stored for this contact on this curator, or None if it wasn't stored."""
    key = contact_key(finding.route_type, finding.value)
    if key is None:
        return None
    domain = email_domain_key(finding.value) if finding.route_type == "email" else None
    if contact_is_excluded(session, key, domain, today):
        return None

    existing = session.scalar(select(Contact).where(Contact.contact_key == key))
    if existing is not None:
        if existing.curator_id != curator.id:
            return None  # the same address already belongs to another curator; leave it with them
        if GRADE_ORDER[finding.confidence] < GRADE_ORDER[existing.confidence]:
            existing.confidence = finding.confidence
            existing.source_url = finding.source_url
            existing.notes = finding.note or existing.notes
        return existing.confidence

    session.add(
        Contact(
            curator=curator,
            playlist_id=playlist.spotify_id,
            route_type=finding.route_type,
            value=finding.value,
            contact_key=key,
            domain_key=domain,
            confidence=finding.confidence,
            source_url=finding.source_url,
            notes=finding.note or None,
        )
    )
    session.flush()
    return finding.confidence


# --- The nightly loop ---------------------------------------------------------------------


def run_research(
    session: Session,
    *,
    pages: PageSource,
    agent: ResearchAgent | None,
    run: Run,
    today: date,
    now: datetime,
    spend_cap_usd: Decimal,
    new_budget: Callable[[], Budget] = Budget,
) -> ResearchSummary:
    summary = ResearchSummary()
    ready: dict[int, int] = defaultdict(int)
    seen_curators: set[int] = set()

    for playlist, profile, fit in _candidates(session):
        if playlist.curator_id in seen_curators or ready[profile.id] >= profile.digest_target:
            continue
        seen_curators.add(playlist.curator_id)
        if not curator_is_eligible(session, playlist.curator_id, today):
            continue
        if _has_strong_contact(session, playlist.curator_id):
            ready[profile.id] += 1
            summary.already_reachable += 1
            continue

        affordable = summary.spend_usd < spend_cap_usd
        summary.budget_reached = summary.budget_reached or not affordable
        summary.researched += 1
        try:
            result = research_lead(
                _lead(playlist, fit), pages=pages, agent=agent if affordable else None, budget=new_budget()
            )
        except Exception as error:
            # Research itself writes nothing, so there's nothing to undo: note it and move on.
            logger.exception("Research failed for playlist %s", playlist.spotify_id)
            summary.errors.append(
                f"{playlist.spotify_id}: {type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]
            )
            continue

        summary.spend_usd += result.spend_usd
        if record_research(session, playlist, result, today=today, now=now):
            ready[profile.id] += 1
            summary.reachable += 1
        elif result.deferred:
            summary.deferred += 1
        else:
            summary.no_contact += 1
        session.commit()

    run.llm_spend_usd = (run.llm_spend_usd or Decimal(0)) + summary.spend_usd
    record_stage(session, run, "research", count_in=summary.researched, count_out=summary.reachable)
    session.commit()
    return summary


def _candidates(session: Session) -> list[tuple[Playlist, Profile, PlaylistProfileFit]]:
    rows = session.execute(
        select(Playlist, Profile, PlaylistProfileFit)
        .join(PlaylistProfileFit, PlaylistProfileFit.playlist_id == Playlist.spotify_id)
        .join(Profile, Profile.id == PlaylistProfileFit.profile_id)
        .where(
            Playlist.status == PlaylistStatus.QUALIFIED,
            Playlist.curator_id.is_not(None),
            PlaylistProfileFit.qualified.is_(True),
            Profile.is_active.is_(True),
        )
        .order_by(
            PlaylistProfileFit.fit_score.desc(), Playlist.last_add_at.desc().nulls_last(), Playlist.spotify_id
        )
    )
    return [(playlist, profile, fit) for playlist, profile, fit in rows]


def _has_strong_contact(session: Session, curator_id: int) -> bool:
    found = session.scalar(
        select(Contact.id).where(Contact.curator_id == curator_id, Contact.confidence.in_(STRONG)).limit(1)
    )
    return found is not None


def _lead(playlist: Playlist, fit: PlaylistProfileFit) -> Lead:
    curator_name = playlist.curator.display_name if playlist.curator else (playlist.owner_name or "")
    return Lead(
        playlist_id=playlist.spotify_id,
        playlist_name=playlist.name,
        playlist_url=PLAYLIST_URL.format(playlist.spotify_id),
        description=playlist.description or "",
        curator_name=curator_name,
        owner_spotify_id=playlist.owner_spotify_id,
        reference_artists=tuple(fit.reference_artists_present or ()),
    )
