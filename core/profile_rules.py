"""The numbers that decide whether a profile is ready to run.

Shared by the YAML importer and the web app so the two can never disagree about what
"ready" means. No heavy imports: the web app on Vercel imports this too.
"""

MIN_GENRES = 1
MIN_REFERENCE_ARTISTS = 3
MIN_TRACKS = 1
MIN_ACTIVE_SEARCH_TERMS = 5

MIN_DIGEST_TARGET = 1
MAX_DIGEST_TARGET = 50
DEFAULT_DIGEST_TARGET = 20

MAX_NAME_LENGTH = 80
