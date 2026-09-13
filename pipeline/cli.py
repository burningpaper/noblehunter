"""Command line for the Mac Mini side of Noble Hunter.

uv run python -m pipeline.cli --help
uv run python -m pipeline.cli profile import config/profiles/example.yaml
"""

from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.profile_config import ProfileConfigError, load_profile_config
from core.profile_import import import_profile
from core.settings import MissingSettingError, load_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Noble Hunter pipeline tools.")
profile_app = typer.Typer(no_args_is_help=True, help="Manage artist profiles.")
app.add_typer(profile_app, name="profile")


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


if __name__ == "__main__":
    app()
