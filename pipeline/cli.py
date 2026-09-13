"""Command line for the Mac Mini side of Noble Hunter.

uv run python -m pipeline.cli --help
uv run python -m pipeline.cli profile import config/profiles/example.yaml
"""

import os
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db_roles import create_app_roles, grant_privileges
from core.profile_config import ProfileConfigError, load_profile_config
from core.profile_import import import_profile
from core.settings import MissingSettingError, load_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Noble Hunter pipeline tools.")
profile_app = typer.Typer(no_args_is_help=True, help="Manage artist profiles.")
app.add_typer(profile_app, name="profile")
db_app = typer.Typer(no_args_is_help=True, help="Database administration (run with the owner connection).")
app.add_typer(db_app, name="db")


def fail(message: str) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code=1)


@profile_app.command("import")
def import_command(path: Annotated[Path, typer.Argument(help="Path to a profile YAML file.")]) -> None:
    """Create or update a profile from a YAML file."""
    try:
        config = load_profile_config(path)
        settings = load_settings()
    except (ProfileConfigError, MissingSettingError) as error:
        raise fail(str(error)) from None

    engine = create_engine(settings.sqlalchemy_url(pooled=False))
    try:
        with Session(engine) as session:
            result = import_profile(session, config)
            session.commit()
    except SQLAlchemyError as error:
        # Name the failure without echoing connection details.
        raise fail(f"Database error while importing {config.name!r}: {type(error).__name__}") from None
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
        raise fail(f"Database error while granting privileges: {type(error).__name__}") from None
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
        raise fail(f"Database error while creating roles: {type(error).__name__}") from None
    finally:
        engine.dispose()

    typer.echo(
        f"Created login roles {web_role!r} and {pipeline_role!r} with least-privilege access.\n"
        f"Connection URLs written to {output} (readable only by you). Passwords were not printed."
    )


if __name__ == "__main__":
    app()
