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
scripts/test-db.sh up        # throwaway Postgres 18 in Docker, localhost only
uv run pytest
uv run uvicorn web.app:app --reload
```

The test suite runs against real Postgres, because the constraints *are* the product: the same curator can't be digested twice in 90 days, and that's enforced by the database, not just by Python. The test fixture drops and rebuilds the schema, so **never point `TEST_DATABASE_URL` at Neon**.

## The database

Neon holds everything. Three sets of credentials exist, and each lives in one place:

| File (all gitignored) | What's in it | Used by |
|---|---|---|
| `.env.local` | `DATABASE_URL` / `DATABASE_URL_UNPOOLED` (the Neon owner) | migrations and admin commands |
| `.env.roles.local` | `WEB_DATABASE_URL`, `PIPELINE_DATABASE_URL` | Vercel (web) and the Mac Mini (pipeline) |
| `spike/.env` | Serper / Brave keys | Stage 0 experiments |

Admin commands, run from the repo root with the owner connection:

```bash
uv run alembic upgrade head                                   # apply migrations
uv run python -m pipeline.cli db grant                        # re-run after every migration
uv run python -m pipeline.cli profile import config/profiles/<name>.yaml
```

The web and pipeline roles were created once with `uv run python -m pipeline.cli db create-roles`. **Never create roles in the Neon console**: Neon makes those members of `neon_superuser`, which can write every table, and that would quietly undo the least-privilege grants. The web role can edit settings and verdicts but can't touch pipeline data. The pipeline role can write data but can't change the schema.

Secrets never go in git. See `.env.example` for the variable names.
