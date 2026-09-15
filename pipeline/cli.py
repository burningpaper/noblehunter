"""Command line for the Mac Mini side of Noble Hunter.

uv run python -m pipeline.cli --help
uv run python -m pipeline.cli profile import config/profiles/example.yaml
"""

import os
import re
import signal
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import httpx
import typer
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db_roles import create_app_roles, grant_privileges
from core.models import Profile
from core.profile_config import ProfileConfigError, load_profile_config
from core.profile_import import artist_for_import, import_profile
from core.settings import MissingSettingError, load_settings
from pipeline.briefs import ClaudeBriefWriter
from pipeline.fit_judge import ClaudeFitJudge, FitJudge
from pipeline.nightly import Stage, describe_run, run_pipeline
from pipeline.recheck import requeue_recent_rejections
from pipeline.report import format_report, qualified_playlists
from pipeline.research_agent import ClaudeResearchAgent
from pipeline.search import BraveProvider, SearchProvider, SerperProvider
from pipeline.settings import PipelineSettings, load_pipeline_settings
from pipeline.stages import digest_stage, research_stage
from pipeline.web import PageFetcher, WebSearch
from pipeline.worker import PipelineRunner, configure_logging, local_schedule, run_forever, tick

SEARCH_TIMEOUT_SECONDS = 20
DEFAULT_REPORT_LIMIT = 50
WORKER_LOG_DIR = Path.home() / "Library" / "Logs" / "noble-hunter"
MISSING_ANTHROPIC_KEY = (
    "ANTHROPIC_API_KEY is not set. Contact research and the digest need it: add it to .env.local."
)

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Noble Hunter pipeline tools.")
profile_app = typer.Typer(no_args_is_help=True, help="Manage artist profiles.")
app.add_typer(profile_app, name="profile")
db_app = typer.Typer(no_args_is_help=True, help="Database administration (run with the owner connection).")
app.add_typer(db_app, name="db")


def fail(message: str) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code=1)


SECRET_PATTERNS = (
    (re.compile(r"PASSWORD\s+'[^']*'", re.IGNORECASE), "PASSWORD '****'"),
    (re.compile(r"SCRAM-SHA-256\$\S+"), "SCRAM-SHA-256$****"),
    (re.compile(r"(postgres(?:ql)?(?:\+\w+)?://)\S+"), r"\1****"),
    (re.compile(r"npg_\w+"), "npg_****"),
)


def describe_db_error(error: SQLAlchemyError) -> str:
    """SQLSTATE and the first line of the server's message, with secret-looking text masked.

    Never includes the SQL statement: for role creation it carries a password.
    """
    original = getattr(error, "orig", None) or error
    first_line = (str(original).splitlines() or [""])[0]
    for pattern, replacement in SECRET_PATTERNS:
        first_line = pattern.sub(replacement, first_line)
    sqlstate = getattr(original, "sqlstate", None)
    label = f"SQLSTATE {sqlstate}" if sqlstate else type(original).__name__
    return f"{label}: {first_line}"


@profile_app.command("import")
def import_command(
    path: Annotated[Path, typer.Argument(help="Path to a profile YAML file.")],
    artist: Annotated[
        str | None,
        typer.Option(
            help="Artist the profile belongs to (created if new). Needed once there's more than one."
        ),
    ] = None,
) -> None:
    """Create or update a profile from a YAML file."""
    try:
        config = load_profile_config(path)
        settings = load_settings()
    except (ProfileConfigError, MissingSettingError) as error:
        raise fail(str(error)) from None

    engine = create_engine(settings.sqlalchemy_url(pooled=False))
    try:
        with Session(engine) as session:
            result = import_profile(session, artist_for_import(session, artist), config)
            session.commit()
    except LookupError as error:
        raise fail(str(error)) from None
    except SQLAlchemyError as error:
        # Name the failure without echoing connection details.
        raise fail(f"Database error while importing {config.name!r}: {describe_db_error(error)}") from None
    finally:
        engine.dispose()

    action = "Created" if result.created else "Updated"
    typer.echo(
        f"{action} profile {config.name!r} (id {result.profile_id}); {result.terms_added} new search term(s)."
    )


@db_app.command("grant")
def grant_command(
    web_role: Annotated[str, typer.Option(help="Role used by the web app on Vercel.")] = "noble_web",
    pipeline_role: Annotated[
        str, typer.Option(help="Role used by the pipeline on the Mac Mini.")
    ] = "noble_pipeline",
) -> None:
    """Give the web and pipeline roles least-privilege access. Re-run after every migration."""
    try:
        settings = load_settings()
    except MissingSettingError as error:
        raise fail(str(error)) from None

    engine = create_engine(settings.sqlalchemy_url(pooled=False))
    try:
        with engine.begin() as connection:
            grant_privileges(connection, web_role=web_role, pipeline_role=pipeline_role)
    except (ValueError, LookupError) as error:
        raise fail(str(error)) from None
    except SQLAlchemyError as error:
        raise fail(f"Database error while granting privileges: {describe_db_error(error)}") from None
    finally:
        engine.dispose()

    typer.echo(f"Granted least-privilege access: {web_role!r} (web), {pipeline_role!r} (pipeline).")


DEFAULT_ROLES_FILE = Path(".env.roles.local")
ROLES_FILE_HEADER = (
    "# Created by `pipeline.cli db create-roles`. Keep private; never commit.\n"
    "# WEB_DATABASE_URL goes into Vercel. PIPELINE_DATABASE_URL stays on the Mac Mini.\n"
)


def write_private_env_file(path: Path, values: dict[str, str]) -> None:
    """Create a brand-new file readable only by the current user. Never overwrites."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(ROLES_FILE_HEADER)
        handle.writelines(f"{key}={value}\n" for key, value in values.items())


@db_app.command("create-roles")
def create_roles_command(
    web_role: Annotated[str, typer.Option(help="Login role for the web app on Vercel.")] = "noble_web",
    pipeline_role: Annotated[
        str, typer.Option(help="Login role for the pipeline on the Mac Mini.")
    ] = "noble_pipeline",
    output: Annotated[
        Path, typer.Option(help="New private file for the connection URLs.")
    ] = DEFAULT_ROLES_FILE,
) -> None:
    """Create the web and pipeline login roles with least privilege. Passwords are never printed."""
    if output.exists():
        raise fail(f"{output} already exists; move it aside first so no credentials are overwritten.")
    try:
        settings = load_settings()
    except MissingSettingError as error:
        raise fail(str(error)) from None

    engine = create_engine(settings.sqlalchemy_url(pooled=False))
    try:
        with engine.begin() as connection:
            urls = create_app_roles(
                connection,
                web_role=web_role,
                pipeline_role=pipeline_role,
                pooled_url=settings.sqlalchemy_url(pooled=True),
                direct_url=settings.sqlalchemy_url(pooled=False),
            )
            # Inside the transaction: if the file can't be written, the roles roll back.
            write_private_env_file(output, urls)
    except (ValueError, LookupError, OSError) as error:
        raise fail(str(error)) from None
    except SQLAlchemyError as error:
        output.unlink(missing_ok=True)
        raise fail(f"Database error while creating roles: {describe_db_error(error)}") from None
    finally:
        engine.dispose()

    typer.echo(
        f"Created login roles {web_role!r} and {pipeline_role!r} with least-privilege access.\n"
        f"Connection URLs written to {output} (readable only by you). Passwords were not printed."
    )


def build_search_providers(settings: PipelineSettings, client: httpx.Client) -> list[SearchProvider]:
    """The real search engines. Tests replace this with fakes."""
    return [
        SerperProvider(settings.serper_api_key.get_secret_value(), client),
        BraveProvider(settings.brave_api_key.get_secret_value(), client),
    ]


def build_claude_stages(settings: PipelineSettings, client: httpx.Client) -> list[Stage]:
    """Contact research, then the digest. Both need Claude. Tests replace this with fakes."""
    if settings.anthropic_api_key is None:
        raise MissingSettingError(MISSING_ANTHROPIC_KEY)
    api_key = settings.anthropic_api_key.get_secret_value()
    pages = PageFetcher(client)
    search = WebSearch(settings.serper_api_key.get_secret_value(), client)
    agent = ClaudeResearchAgent.from_api_key(api_key, pages=pages, search=search)
    return [
        research_stage(pages=pages, agent=agent),
        digest_stage(writer=ClaudeBriefWriter.from_api_key(api_key)),
    ]


def build_fit_judge(settings: PipelineSettings) -> FitJudge:
    """Claude's check on playlists with none of the reference artists. Tests replace this with a fake."""
    if settings.anthropic_api_key is None:
        raise MissingSettingError(MISSING_ANTHROPIC_KEY)
    return ClaudeFitJudge.from_api_key(settings.anthropic_api_key.get_secret_value())


@contextmanager
def open_spotify_client() -> Iterator:
    """Headless Chromium for Spotify. Imported lazily so other commands never need Playwright."""
    from pipeline.spotify import SpotifyWebClient

    with SpotifyWebClient() as client:
        yield client


@app.command("run")
def run_command(
    profile: Annotated[str | None, typer.Option(help="Only search for this profile (by name).")] = None,
    fetch_limit: Annotated[
        int | None, typer.Option(min=1, help="Fetch and judge at most this many candidates.")
    ] = None,
) -> None:
    """Discover, fetch and judge playlists, research curators' contacts, and build the digest."""
    try:
        settings = load_pipeline_settings()
        database_url = settings.sqlalchemy_url()
    except MissingSettingError as error:
        raise fail(str(error)) from None

    engine = create_engine(database_url)
    now = datetime.now(UTC)
    try:
        with Session(engine) as session:
            profile_id = _profile_id_by_name(session, profile) if profile else None
            with httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS) as http:
                # Built before Chromium starts, so a missing key fails fast.
                stages = build_claude_stages(settings, http)
                fit_judge = build_fit_judge(settings)
                with open_spotify_client() as spotify:
                    report = run_pipeline(
                        session,
                        providers=build_search_providers(settings, http),
                        fetcher=spotify,
                        trigger="manual",
                        today=now.date(),
                        now=now,
                        profile_id=profile_id,
                        fetch_limit=fetch_limit,
                        stages=stages,
                        fit_judge=fit_judge,
                    )
        typer.echo(describe_run(report))
    except MissingSettingError as error:
        raise fail(str(error)) from None
    except (LookupError, ValueError) as error:
        raise fail(str(error)) from None
    except SQLAlchemyError as error:
        raise fail(f"Database error during the run: {describe_db_error(error)}") from None
    finally:
        engine.dispose()


@app.command("recheck")
def recheck_command(
    reason: Annotated[
        str, typer.Option(help="The rejection reason to re-check: no-fit, too-small or not-alive.")
    ] = "no-fit",
    days: Annotated[int, typer.Option(min=1, help="Only playlists rejected in the last this many days.")] = 7,
) -> None:
    """Put recent rejections back in the queue, for when the rules that rejected them change."""
    try:
        database_url = load_pipeline_settings().sqlalchemy_url()
    except MissingSettingError as error:
        raise fail(str(error)) from None

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            since = datetime.now(UTC) - timedelta(days=days)
            count = requeue_recent_rejections(session, reason=reason, since=since)
            session.commit()
    except ValueError as error:
        raise fail(str(error)) from None
    except SQLAlchemyError as error:
        raise fail(f"Database error while re-queueing: {describe_db_error(error)}") from None
    finally:
        engine.dispose()

    plural = "playlist" if count == 1 else "playlists"
    typer.echo(f"Put {count} {plural} rejected as “{reason}” back in the queue for the next run.")


@app.command("worker")
def worker_command(
    once: Annotated[bool, typer.Option(help="Check in, do at most one run, then exit.")] = False,
    check: Annotated[bool, typer.Option(help="Only check the settings and the database, then exit.")] = False,
) -> None:
    """The nightly runner: checks in every minute, runs "Run now" requests and the 02:00 nightly run."""
    try:
        settings = load_pipeline_settings()
        database_url = settings.sqlalchemy_url()
    except MissingSettingError as error:
        raise fail(str(error)) from None

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        if check:
            if settings.anthropic_api_key is None:
                raise fail(MISSING_ANTHROPIC_KEY)
            with engine.connect() as connection:
                connection.execute(text("select 1 from worker_status limit 1"))
                connection.execute(text("select 1 from app_settings limit 1"))
            typer.echo("Settings and database look good.")
            return

        typer.echo(f"Logging to {configure_logging(WORKER_LOG_DIR)}")
        runner = PipelineRunner(
            engine,
            open_http=lambda: httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS),
            open_spotify=open_spotify_client,
            providers_for=lambda http: build_search_providers(settings, http),
            stages_for=lambda http: build_claude_stages(settings, http),
            fit_judge_for=lambda: build_fit_judge(settings),
        )
        if once:
            with Session(engine) as session:
                result = tick(
                    session,
                    runner=runner,
                    clock=lambda: datetime.now(UTC),
                    hostname=socket.gethostname(),
                    schedule=local_schedule(),
                )
            typer.echo(result.action if result.error is None else f"{result.action}: {result.error}")
            return

        stop = threading.Event()
        for signal_number in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signal_number, lambda *_: stop.set())
        run_forever(engine, runner, stop=stop)
    except SQLAlchemyError as error:
        raise fail(f"Database error: {describe_db_error(error)}") from None
    finally:
        engine.dispose()


@app.command("report")
def report_command(
    profile: Annotated[str | None, typer.Option(help="Only leads for this profile (by name).")] = None,
    limit: Annotated[
        int, typer.Option(min=1, help="Show at most this many playlists.")
    ] = DEFAULT_REPORT_LIMIT,
) -> None:
    """List qualified playlists, best fit first."""
    try:
        database_url = load_pipeline_settings().sqlalchemy_url()
    except MissingSettingError as error:
        raise fail(str(error)) from None

    engine = create_engine(database_url)
    today = datetime.now(UTC).date()
    try:
        with Session(engine) as session:
            profile_id = _profile_id_by_name(session, profile) if profile else None
            items = qualified_playlists(session, today=today, profile_id=profile_id, limit=limit)
        typer.echo(format_report(items, today=today))
    except LookupError as error:
        raise fail(str(error)) from None
    except SQLAlchemyError as error:
        raise fail(f"Database error while reporting: {describe_db_error(error)}") from None
    finally:
        engine.dispose()


def _profile_id_by_name(session: Session, name: str) -> int:
    ids = list(
        session.scalars(select(Profile.id).where(func.lower(Profile.name) == name.strip().lower()).limit(2))
    )
    if not ids:
        raise LookupError(f"No profile called “{name}”.")
    if len(ids) > 1:
        raise LookupError(
            f"More than one artist has a profile called “{name}”. Rename one in the web app first."
        )
    return ids[0]


if __name__ == "__main__":
    app()
