"""Text comparison helpers shared by the web app and the pipeline (no heavy imports here)."""

import unicodedata


def normalize_text(value: str) -> str:
    """Comparison form: Unicode-normalised, case-folded, single-spaced."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
