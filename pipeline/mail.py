"""The runner's side of reading replies: open each mailbox's Gmail and sync it.

The web app sends; this reads. Both use `core.mail_sync`, so there is one definition of what a
reply is and where it belongs. Everything here is built per round and closed afterwards: a
long-lived HTTP client on a Mac that sleeps is a reconnection problem waiting to happen.
"""

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from core.gmail import Gmail
from core.mail_crypto import mail_cipher
from core.mail_sync import SyncOutcome, sync_all
from core.mailboxes import refresh_token_for
from pipeline.settings import PipelineSettings

logger = logging.getLogger("noble_hunter.mail")

TIMEOUT_SECONDS = 30


def sync_round(engine: Engine, settings: PipelineSettings) -> dict[int, SyncOutcome]:
    """One pass over every connected mailbox. Returns each mailbox's outcome, by mailbox id."""
    problem = settings.mail_problem()
    if problem:
        logger.info("Not reading mail: %s", problem)
        return {}

    cipher = mail_cipher(settings.mail_token_key.get_secret_value())
    with httpx.Client(timeout=TIMEOUT_SECONDS) as http, Session(engine) as session:

        def open_gmail(mailbox) -> Gmail:
            return Gmail(
                http,
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret.get_secret_value(),
                refresh_token=refresh_token_for(mailbox, cipher),
            )

        outcomes = sync_all(session, open_gmail=open_gmail, now=datetime.now(UTC))
        session.commit()

    # `sync_all` never raises -- it collects failures instead -- so a runner watching only for
    # exceptions would call a permanently broken mailbox a clean round. Say what happened.
    summary = describe(outcomes)
    if any(outcome.error for outcome in outcomes.values()):
        logger.warning("%s", summary)
    else:
        logger.info("%s", summary)
    return outcomes


def describe(outcomes: dict[int, SyncOutcome]) -> str:
    """One line for the log or the terminal."""
    if not outcomes:
        return "No mailboxes to read."
    stored = sum(outcome.stored for outcome in outcomes.values())
    problems = [outcome.error for outcome in outcomes.values() if outcome.error]
    parts = [f"Read {len(outcomes)} mailbox(es); {stored} new message(s)."]
    if problems:
        parts.append(f"{len(problems)} problem(s): {problems[0]}")
    return " ".join(parts)
