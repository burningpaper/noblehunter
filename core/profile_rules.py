"""The numbers that decide whether a profile is ready to run, and how big its parts may be.

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
MAX_GENRE_LENGTH = 100
MAX_ARTIST_LENGTH = 200
MAX_ANTI_SIGNAL_LENGTH = 200
MAX_TRACK_TITLE_LENGTH = 200
MAX_TRACK_DESCRIPTION_LENGTH = 300
MIN_TERM_LENGTH = 2
MAX_TERM_LENGTH = 200

# Each active term costs this many search API requests a night: Serper pages 1-3, Brave pages 0-1.
SEARCH_REQUESTS_PER_TERM = 5
