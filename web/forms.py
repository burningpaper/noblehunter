"""Small shared helpers for reading posted form fields."""


def form_id(raw: str) -> int:
    """A posted id, or 0 (which matches nothing) when it isn't a whole number."""
    text = raw.strip()
    return int(text) if text.isascii() and text.isdigit() else 0
