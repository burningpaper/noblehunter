"""The one-way door between the web app and the pipeline.

The web app runs on Vercel; the pipeline runs on the Mac Mini, opens browsers and talks to
search APIs. `core/` is what they share. Nothing in `core/` or `web/` may import `pipeline/`:
if it did, the web app would start dragging in code that has no business on Vercel, and the
split would rot quietly instead of failing loudly.

This exists because it nearly happened -- a Claude caller in `core/` reaching for the
pipeline's token-price helper, which is why that helper now lives in `core/llm_costs.py`.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHARED_PACKAGES = ("core", "web")


def python_files(package: str) -> list[Path]:
    return sorted((ROOT / package).rglob("*.py"))


def imported_modules(path: Path) -> set[str]:
    """Every module this file imports by name, ignoring relative imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize("package", SHARED_PACKAGES)
def test_the_shared_code_never_imports_the_pipeline(package):
    offenders = {
        path.relative_to(ROOT).as_posix(): sorted(
            name for name in imported_modules(path) if name == "pipeline" or name.startswith("pipeline.")
        )
        for path in python_files(package)
    }

    assert {path: names for path, names in offenders.items() if names} == {}


@pytest.mark.parametrize("package", SHARED_PACKAGES)
def test_there_is_something_to_check(package):
    # A glob that quietly matches nothing would make the test above pass for the wrong reason.
    assert len(python_files(package)) > 5
