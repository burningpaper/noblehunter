# Noble Hunter

Every morning, a short list of Spotify playlist curators worth pitching: real people, running playlists that are still alive, where the music fits, with a verified way to reach them.

The hard part of playlist outreach isn't writing the message. It's the ten minutes of tab-hopping per playlist to work out whether anyone is home. Noble Hunter does that part overnight.

## How it's split

- **`web/`** is the settings app, deployed to **Vercel**. Artist profiles, reference artists, search terms, and the approval queue for suggested terms live here.
- **`pipeline/`** is the nightly job, running on a **Mac Mini**. It searches, fetches playlists, qualifies them, finds the curator and writes briefs. It runs for hours and needs a home IP to stay under Spotify's bot detection, which is why it isn't on Vercel.
- **`core/`** is the shared code: database models, config, exclusion rules.
- **Neon Postgres** is the only thing the two halves share.
- **`spike/`** holds the Stage 0 experiments that proved the data sources work. Throwaway code, kept for reference.

See `spec.md` for the why and `IMPLEMENTATION_PLAN.md` for the build stages.

## Local development

```bash
uv sync                      # web app + dev tools
uv sync --group pipeline     # add pipeline dependencies (Playwright)
uv run pytest
uv run uvicorn web.app:app --reload
```

Secrets go in `.env` (see `.env.example`), never in git.
