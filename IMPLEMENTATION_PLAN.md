# Noble Hunter — Implementation Plan

Plan for the Curator Discovery Pipeline in `spec.md`, updated with Jarred's decisions from 2026-09-13. The stages are ordered so the riskiest assumption gets tested first. The first milestone is a real digest produced by hand (end of Stage 7), and scheduling comes only after that.

---

## Decisions so far

| Decision | Choice | Date |
|---|---|---|
| Search providers | **Serper and Brave**, results merged | 2026-09-13 |
| Where settings are edited | **Small web settings app** | 2026-09-13 |
| Hosting | **Split:** web app on **Vercel**, nightly pipeline on the **Mac Mini**, both using one **Neon** database | 2026-09-13 |
| Orchestration | **Plain Python** stage runner (no LangGraph) | 2026-09-13 |
| Cross-profile pitching | A curator appears **at most once per 90 days across all profiles** | 2026-09-13 |
| Verdict effects | `skip` and `pitched`: the curator is eligible again after 90 days. `bad-fit` and `dead`: the curator is excluded **permanently** | 2026-09-13 |
| Re-checks | `no-contact` and not-alive rejections are re-checked after 90 days. Pay-to-play and bot (`real`) rejections are permanent | 2026-09-13 |
| Repeat playlists | The same playlist **may** be pitched again for the same profile after 90 days (still bound by the curator's 90-day rule) | 2026-09-13 |
| Digest and verdicts | **Digest page in the web app** with verdict buttons, plus a short morning **email linking to it** | 2026-09-13 |
| Web login | Sign in with Google. Admins are listed in ALLOWED_EMAILS; other people are added to an artist on the People page | 2026-09-15 |
| Several artists | Other artists can use Noble Hunter. Each person sees only their own artists' profiles, digests and mail; admins see everything. Curators are separate per artist (own 90-day window and bad-fit list; dead is shared) | 2026-09-15 |
| Visual direction | **Dark studio**: near-black ground, one restrained accent, clean sans-serif, generous spacing, subtle motion | 2026-09-13 |
| Configuration | **Everything is configured in the web app**, per profile (and per search term where it applies). YAML import is only a bootstrap/backup tool; Jarred enters Synman himself | 2026-09-14 |
| Minimum size | Playlists **under 50 followers aren't worth pitching**. The floor is a per-profile setting (default 50). Too-small playlists are re-checked later, since they can grow | 2026-09-14 |
| Contactability | **No contactable person, no lead.** Service and automated owners (playlist generators, stats sites, chart accounts) and playlists with no findable contact are dropped | 2026-09-14 |
| Claude | **Required, not optional.** Stage 6 contact research is an agentic Claude loop (search, follow links, read bios). `ANTHROPIC_API_KEY` goes in `.env.local` on the Mac Mini, and the same key goes in Vercel's Production environment for Ask Claude (Jarred's choice: one key, not two) | 2026-09-14 |
| Ask Claude | An "Ask Claude" button in the genres, reference artists, anti-signals and search terms sections. Ticked suggestions are added directly. The web app still starts without the key; the button then explains what's missing | 2026-09-14 |
| Claude spend | Nightly cap $2 by default, editable in the web app. Target is under $2 a night | 2026-09-14 |
| Fit without reference artists | Requiring a reference artist on the playlist is **too restrictive** (Jarred). An alive, real playlist with none of them can still qualify: first if its name or description uses one of the profile's genres (free), otherwise if Claude judges its artist list, name and description to fit (counted in the nightly budget). Anti-signals still reject. These matches **rank below** reference-artist matches. This week's "no fit" rejections go back in the queue to be judged again | 2026-09-14 |
| Deploy | Profile editor and pipeline code pushed 2026-09-14 with Jarred's OK | 2026-09-14 |
| Database roles | `noble_web` and `noble_pipeline` are **created with SQL by `pipeline.cli`, never in the Neon console**. Neon adds console/CLI/API roles to `neon_superuser` (`pg_write_all_data`, `CREATEROLE`, `BYPASSRLS`), which would make least-privilege grants meaningless | 2026-09-13 |
| Repo / deploy | `github.com/burningpaper/noblehunter` (public, by Jarred's choice). Vercel project `noblehunter` with framework `fastapi`, live at `noblehunter.vercel.app`, Neon linked | 2026-09-13 |
| Profiles | **Multiple profiles** (e.g. per release or project), each with its own genres, reference artists, anti-signals, tracks and search terms | 2026-09-13 |
| Search terms | **Manual and suggested.** Jarred writes terms; Claude proposes new ones, which wait for approval | 2026-09-13 |

Because of these, the spec's static YAML inputs (§2) become **database tables edited through the web app**. Fit scoring and outreach are **per profile**. Liveness and pay-to-play checks stay per playlist, since they don't depend on the profile.

---

## What Stage 0 proved (2026-09-13)

**Spotify data, logged out.**
- **Playlists:** `spike/fetch_playlists.py` fetched 10 of 10 playlists completely. It loads the playlist page once in headless Chromium, captures the `fetchPlaylist` GraphQL call, then replays `fetchPlaylistContents` by offset. Scroll-driven paging silently skipped tracks, so it's not used.
- **Curator profiles:** `spike/fetch_profile.py` pages `user-profile-view/v3/profile/<id>/playlists` with `Accept: application/json`. Stop only on an empty page and de-duplicate by URI. Profiles also list playlists owned by others, so filter on `owner_uri`.
- **Big playlists are slow:** 5,931 tracks took 194 s. Tracks aren't in date order, but reading the first page plus the last ~300 tracks worked for the spike.
- **Bot detection:** the page loads Google reCAPTCHA Enterprise. There was no challenge at spike volume; keep an eye on it.

**Search.**
- 5 seeds × ~30 results: Serper found 130 unique playlists and Brave 138. Overlap was only 0–9 per seed.
- Quality sample of 79 playlists: good playlists (alive plus a reference artist) came out Serper-only 2, Brave-only 3, both 3. Brave's results were fresher (median last add 359 vs. 831 days).
- **Serper results vary between runs:** a short page is not the end of the results, so log every page. `num=30` in one call returns nothing; use `page` 1–3.

**Yield warning.** Only **~10%** of search-found playlists were alive and contained a reference artist, measured with a *stand-in* artist list. The spec assumes 20–30%. Neighbour discovery, more search terms and both providers are needed from week one. Re-measure once real profiles exist.

---

## Recommended stack

| Concern | Choice | Why |
|---|---|---|
| Language / tooling | Python 3.12, `uv`, `ruff`, `pyright`, `pytest` | One language for pipeline and web app. Playwright and the Anthropic SDK are first-class in Python. |
| Orchestration | **Plain Python stage runner**, CLI via `typer` | Each stage's table is its checkpoint, so re-running is safe. Only one stage is agentic. *(Open question 4.)* |
| Repo layout | One repo, three packages: `core/` (models, config, exclusion), `web/` (deployed to Vercel), `pipeline/` (runs on the Mac Mini). `uv` dependency groups keep Playwright out of the Vercel bundle | Shared models without shipping a browser to Vercel. |
| Database | Neon Postgres (linked via Vercel's Neon integration), SQLAlchemy 2.0 + Alembic, psycopg 3 | The web app uses Neon's **pooled** connection string with no client-side pool, since Vercel starts many short-lived instances. The pipeline and Alembic migrations use the **direct** connection. A Neon branch gives a disposable test DB. |
| Database roles | Separate Neon roles for `web` and `pipeline` | Least privilege: the public-facing app can edit settings and verdicts but can't touch run internals or drop tables. |
| **Settings web app** | **FastAPI + Jinja2 templates + HTMX** (pinned copy stored in the repo, no CDN), **hand-written CSS with design tokens** (no build step), **Authlib** for Google OpenID Connect, signed session cookies | Server-rendered pages in the same codebase and against the same models as the pipeline. HTMX gives inline editing, approve/reject buttons and smooth partial updates without a separate JavaScript frontend to maintain. Tailwind was dropped: a CSS build on Vercel's Python builder adds a moving part for no real gain at this size. Authlib handles OAuth state, nonce and ID-token verification rather than hand-rolling them. |
| Web app hosting | **Vercel** (FastAPI on the Python runtime), deployed from git | Reachable from anywhere, with no inbound ports on the home network. Because it's public, it gets a real login (Stage 2). |
| Spotify fetch | Playwright (Chromium) with GraphQL replay, as proven in Stage 0 | |
| Web search | Serper + Brave behind one `search()` interface, merged and de-duplicated, with per-page logging | Decision above. |
| Social lookup | Bluesky public API. Instagram and X only through search-engine `site:` queries | Matches spec §8. |
| LLM | Anthropic Python SDK, `claude-opus-5` by default, structured outputs (Pydantic), prompt caching on each profile's prefix | Used for genre fit, the contact agent, briefs and **search-term suggestions**. |
| Contact agent | Manual tool-use loop with client-side tools and a hard budget | The spec requires a 10-fetch / 60 s cap. |
| Pipeline hosting and scheduling | **Mac Mini**: `launchd` at 02:00, `pmset` wake, a lock file so runs can't overlap. A small launchd poller checks `run_requests` every few minutes for "Run now" requests from the web app | The pipeline runs for hours, beyond Vercel's function limits (300 s Hobby / 800 s Pro). The home IP is also far less likely to trip Spotify's reCAPTCHA than cloud IPs. The Mac Mini only makes outbound connections. |
| Secrets | Web: Vercel environment variables (database URL, session secret, login credentials). Pipeline: `pydantic-settings` + `.env` on the Mac Mini (gitignored) | Serper, Brave and Anthropic keys live **only** on the Mac Mini, since the web app never calls those services. |
| Observability | `structlog`, a `runs` table with per-stage counts and spend | These feed the digest footer and the web app's run history. |

### LLM cost levers
- **Cheap checks first.** Deterministic gates and the code-first contact ladder run before any model call.
- **Batches.** Genre classification can use the Message Batches API at 50% cost.
- **Claude spend target: under $2 a night** (revised 2026-09-14 from a pessimistic $20–30). The spec's free steps (description, links, other playlists) go first. Only playlists likely to make the digest are researched, once per curator, best fit first, stopping when the digest is full. Pages reach Claude as trimmed text with routes pre-extracted, and search uses Serper/Brave. The model (Haiku 4.5 or Sonnet 5) is chosen by running both on the ~20 labelled playlists. The nightly cap defaults to **$2** and is editable in the web app; reaching it stops research and the run reports what was left.

---

## Data model (additions to spec §5)

```
profiles              id, name, is_active, digest_target, created_at, updated_at
profile_genres        profile_id, tag, priority
reference_artists     profile_id, display_name, normalized_name, spotify_artist_id (nullable)
anti_signals          profile_id, kind (artist | term), value, normalized_value
profile_tracks        profile_id, title, spotify_url, one_line_description
search_terms          id, profile_id, term, origin (manual | suggested), status (proposed | active | paused | rejected),
                      rationale, created_at, decided_at
playlist_sources      playlist_id, search_term_id (nullable), provider (serper | brave | neighbour), run_id, rank
playlist_profile_fit  playlist_id, profile_id, fit_score, reference_artists_present, genre_tags, qualified
runs / run_stage_counts   per-run, per-profile stage counts, search calls, LLM spend, started_at / finished_at / heartbeat_at
run_requests          id, requested_at, requested_by, scope (all | profile_id), status (pending | claimed | done | failed), claimed_at
contacts              + contact_key (normalised, unique) for exclusion
outreach              + profile_id
```

`playlists` keeps the per-playlist facts from the spec (alive, real, size band, owner). Spotify-run owners (`spotify`, `thesoundsofspotify`) are filtered before any fetch.

---

## Stage 0: Spike — prove the data sources
Goal: Prove we can fetch complete playlist and curator data without login, and choose a search provider.
Success Criteria: Every required field appears for ≥9 of 10 playlists. We can list curator profiles. A provider is chosen with recorded counts.
Status: Complete.

## Stage 1: Foundation
Goal: Repo skeleton with `core/`, `web/` and `pipeline/` packages and `uv` dependency groups, settings loading, Alembic schema for all tables above, separate Neon roles for web and pipeline, the exclusion module, and a `profile import` CLI that loads a YAML profile into the DB, so pipeline work doesn't wait on the web app.
Success Criteria: `uv run pytest` passes. Migrations apply cleanly to a Neon branch. Exclusion tests cover handle and email normalisation, freemail domains, the 90-day `no-contact` window, curator-with-outreach, and cross-profile behaviour. Unique constraints make a duplicate digest entry impossible.
Status: Complete (2026-09-13). The first real profile is still to import, pending Synman's details.
- ✅ Contact normalisation, settings (`.env` + `.env.local`), profile YAML validation.
- ✅ Models and first migration (15 tables). The outreach EXCLUDE constraint enforces the 90-day curator rule, and a mutation check confirmed it's load-bearing.
- ✅ Exclusion rules (verdicts, re-checks, contact and domain blocking) and profile import CLI.
- ✅ Least-privilege roles: `db create-roles` (SQL-created, SCRAM, credentials to a 0600 file) and `db grant`.
- ✅ 196 tests against real Postgres 18 in Docker (`scripts/test-db.sh up`).
- ✅ Neon: migration `0001` applied to the (empty) main database. All 15 tables, `btree_gist`, the EXCLUDE constraint and the special indexes were verified.
- ✅ Neon: `noble_web` and `noble_pipeline` created with `db create-roles`, with URLs in `.env.roles.local` (mode 600). Live checks: web can read and insert profiles but is refused on playlists, migration history and schema. Pipeline can write playlists but is refused on migration history and schema.
- ⏳ Import Synman's real profile once Jarred sends genres, reference artists, tracks and anti-signals. Jarred chose not to import a placeholder.

## Stage 2: Settings web app
Goal: A polished web app, deployed to Vercel, to create, edit, activate and pause profiles, and to manage genres (ordered), reference artists, anti-signals, tracks and manual search terms. It includes login, a "Run now" button and a pipeline status panel.
Success Criteria:
- Server-side validation: a profile can't be activated without ≥1 genre, ≥3 reference artists, ≥1 track and ≥5 active terms. Duplicate terms and artists are rejected after normalisation. Clear inline error messages.
- Estimated searches per night per profile is shown against the provider quota.
- Responsive and keyboard-navigable, with visible focus states, a meaningful transition when an item is saved or deleted, and loading feedback on every action.
- Route tests (pytest + FastAPI TestClient) cover happy path, validation failures and concurrent edits. A Playwright browser check covers the main flows.
- **Security (public internet):** every route except `/health`, `/login` and the OAuth callback requires a session. Only a Google account with a *verified* email on the allow-list gets in. OAuth state and nonce are verified. Cookies are `Secure`, `HttpOnly` and `SameSite=Lax`, and are signed with `SESSION_SECRET`. Forms and HTMX requests carry CSRF tokens. Failed sign-ins are logged without leaking details. Tests prove unauthenticated requests are refused on every route. There are no passwords to rate-limit, because Google handles credentials.
- **Pipeline status panel:** shows the last run's start and finish times, counts and heartbeat. It warns if no run has finished in 26 hours, which is how a switched-off Mac Mini becomes visible.
- **Run now** inserts a `run_requests` row. A second click while one is pending does nothing.
- **Deploy:** the production deploy works against Neon as `noble_web` via `WEB_DATABASE_URL` (pooled). Google sign-in works on `noblehunter.vercel.app` and `localhost:8000` only, because preview URLs change and Google needs exact redirect URIs. The Vercel bundle contains no Playwright or Chromium.

Steps, each test-first and committed separately:
- ✅ **2a. Web foundation** (`7fae4c9`): app factory, web settings (`WEB_DATABASE_URL`, `SESSION_SECRET`, Google client, allowed emails), per-request DB session, base layout and dark-studio design tokens, the stored htmx copy, friendly 404/500 pages.
- ✅ **2b. Sign in with Google** (`9cb409a`, live on Vercel and verified by Jarred): login, callback and logout. Allow-list plus verified email, the session guard on every route, and CSRF.
- ✅ **2c. Profiles** (`70309ca`): list, create, rename, set digest target, activate and pause, with the activation rules shown inline.
- ✅ **2d. Profile contents** (committed 2026-09-13 overnight, not yet pushed): genres (ordered), reference artists, anti-signals, tracks and manual search terms, edited inline with HTMX, with the searches-per-night estimate. An active profile pauses itself if an edit leaves it incomplete.
- **2e. Run now and the status panel:** `run_requests` insert (safe to repeat), last run and heartbeat, and a staleness warning.
- **2f. Ship:** deploy, check the main flows in a real browser with Playwright, and polish accessibility and motion.

Status: In Progress (started 2026-09-13).

## Stage 3: Discover
Goal: For each active profile, run its active search terms through Serper and Brave, merge and de-duplicate, filter Spotify-run owners, record `playlist_sources`, and dedupe against exclusion before any fetch.
Success Criteria: A dry run yields 150–300 candidates across active profiles. Re-running the same night adds zero duplicates. Short pages are logged as warnings. Tests use recorded provider responses, including a short-page case.
Status: In Progress.
- ✅ Search layer `pipeline/search.py` (`6e51053`, merged in `797fdaa`): Serper and Brave, merged candidates, Spotify-editorial filter, warnings and errors that never leak keys. 53 tests. Live check on "glitchy ambient": 27 + 36 playlists, 4 shared, 59 merged.
- ✅ Discover step `pipeline/discover.py` (`9216bc0`): candidates plus attribution per term and run, exclusion applied before any fetch, and re-check windows honoured.
- ✅ Spotify fetcher `pipeline/spotify.py` (`8a858de`, merged in `c91eb4a`), with 96 unit tests and 4 live tests.
- ✅ Evaluate and qualify `pipeline/evaluate.py` and `pipeline/qualify.py` (`c2d586c`): alive, real and fit checks, plus the new `spotify-owned` reason (migration 0002).
- ✅ `python -m pipeline.cli run` / `report` (`6e79061`).
- ✅ First real run (local `noble_browser` database, stand-in example profile, 2026-09-14): 5 terms found 274 playlists (261 new); 15 fetched, 0 qualified (9 dead, 6 off-genre). The rejections were checked by hand and are correct.
- ✅ Overnight batch: 149 fetched, **15 qualified (10%)**, 121 dead, 21 no-fit, 8 Spotify-owned, 1 vanished. Spotify never pushed back (21 minutes).
- ⏳ Found by reading the leads, and needing Jarred's call: (1) a minimum-followers floor, since many qualifiers are personal playlists with under 100 followers; (2) an owner filter or reachability tag for automated and service accounts (Chosic, volt.fm, chart and label accounts); (3) one pitch per curator in the digest (arcticdrones owns two qualifiers).
- Note: "The Sound of …" playlists belong to Spotify's `thesoundsofspotify` account but don't use the `37i9dQZF1` ID prefix, so they have to be filtered by owner at fetch time.

## Stage 2f: Ask Claude in the profile editor
Goal: A small "Ask Claude" button in the profile editor's sections opens a prompt field (e.g. "What search terms would find IDM playlists on Spotify?"). Claude replies with a list of options, each with a one-line reason. Jarred ticks the ones he wants and clicks "Add selected"; they're added through the same validated path as typed entries. Search terms are stored with origin `suggested`. Ticking a suggestion counts as approving it, so it goes straight to active.
Design:
- Claude sees the profile's current genres, artists, anti-signals and terms, so a prompt like "more artists like these" works. Suggestions already on the profile are filtered out in code.
- One structured-output call (Pydantic) per ask: `claude-opus-5` for its music knowledge, at low effort for speed, with a bounded `max_tokens`. That's about 1–3 cents an ask.
- It runs in the web app on Vercel, so `anthropic` moves into the web dependencies and Vercel needs `ANTHROPIC_API_KEY`. The suggester is injected like the identity provider, so tests never call the API. An opt-in live test checks the real call.
- Errors appear inline ("Claude didn't answer, try again") and are logged. Nothing is added unless Jarred ticks it.
Success Criteria: From an empty section, ask, tick, add, and the items appear with the section's counts updated. Existing entries never come back as suggestions. An API failure leaves the section usable. Keyboard and screen-reader friendly, with a loading state while Claude thinks.
Status: In Progress

## Stage 4: Fetch and parse
Goal: The Stage 0 fetch approach made production-grade: rate limit, retry then `fetch-failed`, a track ceiling / tail read for huge playlists, and a parser working from trimmed fixtures.
Success Criteria: Parser tests pass against trimmed fixtures (including >100 tracks and a partial read). A failed fetch doesn't stop the run. The rate limit is verified in a test.
Status: In Progress — `pipeline/spotify.py` and `pipeline/evaluate.py` built and proven on a 149-playlist live batch.

## Stage 5: Qualify
Goal: Per playlist: alive, real, reachable, size band. Per profile: fit (reference-artist overlap, anti-signal hard reject, LLM genre match) written to `playlist_profile_fit`.
Success Criteria: A hand-labelled set of ~40 playlists per real profile gets ≥85% agreement and zero anti-signal false passes. Heuristic edge cases are unit-tested. Yield is re-measured with real reference artists.
Status: In Progress — alive/real/fit heuristics and the per-profile minimum-followers floor (default 50, `too-small`, migration 0003) are done. The LLM genre match and the labelled-set check wait for the real Synman profile.

## Stage 6: Contact resolution
Goal: A code-first ladder, then the budgeted agent, producing A/B/C contacts with route type and source URL.
Success Criteria: The budget is enforced in tests (10 fetches / 60 s). On ~20 labelled playlists, no C contact is graded A or B, and every A has a source URL containing the contact. The spend cap aborts gracefully.
Status: In Progress
- 6a — Extract routes from text (spec step 1): emails (including `[at]`/`[dot]`), platform-hinted handles, social URLs, form links, and links worth following. Pure functions, grounded in real descriptions from the first batch. A route the curator states in their own description is grade A.
- ✅ 6a — Extract routes from text (`0875ea6`).
- 6b — Web tools (`pipeline/web.py`): fetch a page over plain HTTP, returning visible text plus link targets (Linktree buttons are links). http/https only, and private, loopback and link-local addresses are refused (including after redirects). Time, size and content-type limits apply. Also a general Serper web search (title, URL, snippet).
- 6c — The ladder (`pipeline/research.py`), in two parts. **Free steps:** the description (A, sourced from the playlist page), then its links, which count against the per-playlist budget (A, sourced from the linked page). **Then the agent,** only if the free steps found no A. It's a manual Claude tool-use loop with `web_search`, `fetch_page` and a final `report_findings` tool (strict schema). Limits are 10 fetches, 60 s and a few turns per playlist, plus the nightly $ cap. **Code verifies every claim:** an A must appear on the fetched source page, a B must appear in something the agent actually saw, and anything else becomes C. **Recording:** contacts are stored per curator; excluded contact keys are skipped; A/B keeps the playlist `qualified` (digest-ready); otherwise it becomes `no-contact`, which is re-checked in 90 days. Service accounts count as no-contact. **Which playlists get researched:** those for active profiles and eligible curators, one playlist per curator, best fit first, stopping once each profile has `digest_target` digest-ready curators or the budget is spent. The model starts as `claude-sonnet-5`; compare it with Haiku on labelled playlists later.
- 6d — The owner's other playlists (spec step 4) as an extra free step. This comes later, because each one costs a Spotify fetch.

## Stage 7: Brief, rank and digest — first real digest
Goal: Per-profile briefs that name the profile's reference artists and closest track, weighted ranking, a digest grouped by profile with a stage-count footer, and `outreach` rows with status `new`.
Success Criteria: A manual end-to-end run meets the spec's success condition per profile (≥10 Jarred would write to, <2 rejected on sight, 0 repeats). When fewer than 10 qualify, the digest says so plainly.
Status: In Progress
- 7a — `pipeline/digest.py`: candidates ranked across profiles by a weighted sum (fit, then contact confidence, recency, size band), so each curator goes to their best profile. Take the top `digest_target` per profile. A Claude brief (80–120 words plus a suggested angle, structured output) quotes what the curator says they want and picks the closest track. If Claude fails, a plain template brief is used, so the digest still ships. Writes an `outreach` row with status `new` and marks the playlist `digested`, committed per entry; the 90-day EXCLUDE constraint is the backstop.
- 7b — `app_settings` (migration 0005): the nightly Claude budget (default $2), editable in the web app. Research and digest become stages of `run_pipeline` (worker and CLI), with spend recorded on the run.
- 7c — Digest page (`/digest`): entries grouped by profile, with contact routes as links, brief and angle. Pitched, skip, bad-fit and dead buttons go through `record_verdict`. The footer shows the night's stage counts, and says so plainly when there are fewer than 10 entries. A "Digest" nav link. The morning email comes later.
- **Built 2026-09-14:** web tools `19d7ec0`, research ladder `78e31a6`, agent, digest and briefs `0799a01`, nightly wiring and budget `938c9a7`, digest page `c5a42ad`. Live checks: Ask Claude gave 12 relevant search terms in 9 s. A brief came back in the spec's shape (104 words, $0.005). The agent found a real curator's own Instagram and Linktree (both A) in 43 s for $0.048, using 5 of 10 fetches, so the $2 cap covers about 40 researched playlists a night. **Still to do:** the first real digest from a nightly run, judged against the success condition; the morning email; the owner's other playlists (6d).

## Stage 8: Scheduling and operations
Goal: The nightly runner on the Mac Mini, which is the Mac Jarred develops on (confirmed 2026-09-14). Built together with Stage 2e: "Run now" and the status panel in the web app.
Design:
- **One long-lived worker,** `python -m pipeline.cli worker`, kept alive by a launchd LaunchAgent: it starts at login and restarts if it dies. That's simpler than two launchd jobs, because one process owns the schedule, the "Run now" queue, the lock and the heartbeat.
- **Every minute** it writes a heartbeat to a one-row `worker_status` table (migration 0004). Then it either claims a pending `run_requests` row (`FOR UPDATE SKIP LOCKED`), or starts the nightly run if it's past 02:00 local and no scheduled run has started today. The same rule catches up after the Mac was asleep at 02:00.
- **No overlaps:** a Postgres advisory lock stops two runs overlapping, even if a second worker is started by mistake. During a run the heartbeat keeps ticking from a background thread, so a long run doesn't look dead.
- **Logs** go to `~/Library/Logs/noble-hunter/`, rotated. Search keys come from `.env.local`; launchd never sees `spike/.env`.
- **Web app:** a "Run now" button (clicking twice is harmless) and a status panel. The panel shows whether the runner is online (offline once the heartbeat is more than 3 minutes old), the current run, and the last run with its counts. It warns when the last run failed or nothing has finished in 26 hours. Alerts are web-app only, which is Jarred's choice.
- **Install:** `scripts/install-worker.sh` writes and loads the LaunchAgent. Waking the Mac at 01:55 (`sudo pmset repeat wake`) is optional, and Jarred runs it himself.
Success Criteria: Three consecutive unattended nights complete. A deliberately broken stage leaves the earlier stages' work intact and the web app shows the failure. "Run now" starts a run within 2 minutes. Pulling the network mid-run shows the runner as offline in the web app, with no corrupted rows. Unit tests cover the schedule rule, claiming requests, overlap refusal and the status panel's warnings.
Status: In Progress

## Stage 9: Feedback, neighbours and search-term suggestions
Goal:
- Ingest verdicts. `pitched` feeds neighbour discovery; `bad-fit` and `dead` exclude the curator.
- A weekly job where Claude proposes new search terms per profile from its genres, reference artists and the names and descriptions of pitched playlists. Each proposal has a one-line rationale.
- An **approval queue** in the web app (approve, edit then approve, reject). Rejected terms are never re-proposed.
- The term performance report (pitched vs. rejected per term) shown in the web app.
Success Criteria: Tests cover each verdict's effect and that suggestions never duplicate active or rejected terms after normalisation. Suggested terms only run after approval. The report matches hand-counted fixtures.
Status: Not Started

## Stage 10: Tuning week
Goal: Review a week of failures, mainly contact resolution and yield per profile, and adjust.
Success Criteria: Yield is measured daily against the spec's first-week expectations. Changes are logged in `.claude/DEVELOPER_LOGS.md`.
Status: Not Started

## Stage 11: Artists and access
Goal: Several artists use Noble Hunter without seeing each other's work or competing for curators.
Success Criteria: Plan tasks in docs/superpowers/plans/2026-09-15-artists-and-access.md pass; a member of one artist gets 404 for every route on another artist's data; the same curator can reach two artists' digests on one night.
Status: In Progress (stage 1 of 3 complete)

---

## Open questions

1. **Digest and verdicts in the web app?** Now that there's a web app, the digest and the `pitched` / `skip` / `bad-fit` / `dead` buttons could live there, with an optional morning email linking to it. Recommended, as it's one place for everything, but it replaces the spec's email or Markdown options.
2. **Login method.** The web app is public on Vercel. Options: a single-user password (simplest), a magic-link email, or "Sign in with Google" restricted to your address (no password to manage). Vercel's own deployment protection can be added on top on a paid plan.
3. **Pitching across profiles.** If a curator is pitched for profile A, can they be pitched for profile B? Recommendation: no more than once per 90 days across all profiles, to protect your reputation with curators.
4. **LangGraph or plain Python?** Recommendation: plain Python, unless the existing Mac Mini setup has shared LangGraph infrastructure worth matching.
5. ~~**Nightly LLM spend cap.**~~ Decided 2026-09-14: $2 a night by default, editable in the web app.
6. **Existing code.** Is there an existing repo, Neon project or conventions on the Mac Mini to reuse?
7. **First real profile.** Synman's genres, reference artists, tracks and anti-signals. Used to seed Stage 1 and re-measure yield.
