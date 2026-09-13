# Developer Logs

## 2026-09-13: Planning, and the page that wasn't there

The spec says Spotify's public playlist page is readable with a plain fetch. Before building on that, we tested it. The page came back as 162 KB of JavaScript shell with a title and nothing else. The embed page did better: a `__NEXT_DATA__` blob with the name, owner and first 50 tracks. It still had no added dates and no follower count, and those are exactly the two things the Alive and Real checks need.

So the plan starts with a spike (Stage 0). Playwright loads the web player and captures its internal GraphQL responses instead of scraping the DOM. If that fails, the design changes before any real code is written.

Other decisions in `IMPLEMENTATION_PLAN.md`:
- **Plain Python stage runner instead of LangGraph.** Each stage's table is already its checkpoint, so a graph framework would duplicate that. Still open for Jarred to confirm.
- **Cheap checks before LLM checks.** Deterministic gates and the code-first contact ladder run before any model call. The contact agent is the main cost driver.
- **Gaps found in the spec:** contact-level exclusion keys, curator identity, seed-query attribution, stage-count storage, re-check policy for rejected playlists, and a ToS/account-safety note (never scrape logged in).

No code yet. Waiting on answers to the five open questions.

## 2026-09-13: The spike works, and scrolling lied

We pointed headless Chromium at a 127-track IDM playlist, logged out, and watched the web player's own API calls go by. One of them, `fetchPlaylist`, had everything the spec wants: description, followers, owner ID, and an `addedAt` on every track. It only covered the first 25 tracks, though.

The first fix was scrolling so the player loads the rest, and it seemed to work. Counting the captured tracks told a different story: 108 of 127. The player had quietly skipped a whole page. A pipeline built on that would have reported playlists as dead when their newest adds were simply never fetched. In this playlist the two most recent adds were buried mid-list.

So we stopped scrolling. `spike/fetch_playlists.py` now loads the page once, keeps the auth headers and persisted-query hash from the player's own request, and asks for each page by offset. Result: 10 of 10 real playlists fetched completely, from 21 to 5,931 tracks, no login.

Two things this showed:
- **Big playlists are slow.** A 5,931-track list took over three minutes at one page per second. Stage 3 needs a track-count ceiling or an early stop.
- **Search results skew dead.** Only 2 of the 10 would pass the Alive rule, so neighbour discovery is doing more of the work than the spec implies.

We also looked at Spotify MCP servers and the Claude Spotify connector. Both sit on the official Web API under a personal login, which the spec excludes. Neither would be available to an unattended nightly job anyway, so they aren't part of the design.

## 2026-09-13: Finding a curator's other playlists

Neighbour discovery depends on one question: what else has this curator made? The first probe of a profile page found nothing, and the screenshot showed only a footer, because it was taken after scrolling to the bottom. Taken before scrolling, it showed the whole profile: arsendis, 402 public playlists.

The data turned out to be hidden in plain sight. The capture filter included the right host, but the web player asks for **protobuf** and the probe only kept responses it could parse as JSON. When the same endpoint is asked for `application/json`, it returns JSON.

Then came the traps, each caught only because we checked counts instead of trusting a 200:
- **The main profile call stops at about 400 playlists** and ignores offset parameters. The "Show all" sub-endpoint (`/profile/<id>/playlists`) pages properly.
- **Its pages aren't a fixed size.** The loop stopped at 199 because it treated a short page as the last one. It now stops only on an empty page, then de-duplicates by URI because pages overlap at the edges. Result: 401 unique of 402 stated.
- **A curator's public playlists aren't all theirs.** 30 of arsendis's belong to other people, Spotify included. Filter on `owner_uri`. The others are interesting too, since they show which curators this curator follows.

Still open: comparing Serper and Brave, which needs API keys. `spike/compare_search.py` is written and exits cleanly without them. Added `.gitignore` and `.env.example` so keys stay out of the repo.

## 2026-09-13: Two search engines, one uncomfortable number

The first comparison looked decisive: Brave 138 playlists, Serper 105. It was wrong. Serper had quietly returned 9–10 playlists for two seeds, and on a re-run the same searches gave 26–29. There were no errors and nothing odd in the status codes. The pages were just short that time. With per-page logging added, the honest count was Serper 130, Brave 138. The lesson carries into production: a short page of search results is a warning to log, not the end of the results.

Counts don't answer the real question, which is whether these are playlists worth pitching. So we sampled ~30 playlists found only by Serper, ~30 found only by Brave, and all 21 found by both, then fetched every one. We scored each on the spec's Alive rule and a stand-in list of reference artists. It took about 35 minutes in headless Chromium, slower than planned because big playlists need many pages and we wait politely between them.

Brave's results were fresher (median last add 359 days vs. 831). The number of genuinely good playlists was a tie: 2 from Serper, 3 from Brave, 3 from both. The providers barely overlap, so we recommend using both.

The uncomfortable number: **8 of 79.** Only about 10% of search-found playlists were alive and contained a reference artist. The spec plans on 20–30%. At 10% the digest falls short of 10 on most nights unless neighbour discovery and a wider seed list do real work from the start. It's better to learn that in a spike than in week one.

Stage 0 is complete. Next are Jarred's answers to the open questions, then Stage 1.

## 2026-09-13: The YAML file becomes a web app

The spec imagined the artist profile as a small file, edited rarely. Jarred wants more than that: several profiles, each with its own genres, reference artists and search terms, edited through a web page, with the system proposing new search terms for him to approve.

That changes the shape of the build more than any single feature:
- **Config moves into the database.** Profiles, genres, artists, anti-signals, tracks and search terms become tables the pipeline reads and the web app writes.
- **Fit is now per profile.** A playlist can be a great match for one project and wrong for another. Liveness and pay-to-play checks don't depend on the profile, so they stay per playlist. Fit gets its own table.
- **Search terms have a lifecycle:** proposed, active, paused or rejected, plus an origin (manual or suggested). A suggestion never runs until Jarred approves it, and a rejected term is never proposed again.

Stack for the web app: **FastAPI, Jinja2 and HTMX**, in the same Python codebase and against the same models as the pipeline. A separate JavaScript frontend would mean two languages, two builds and two sets of models for what is essentially a set of forms and an approval queue. HTMX still gives inline editing, partial updates and transitions.

Plan restructured: settings web app is Stage 2, and search-term suggestions are folded into the feedback stage. Stage 1 gets a YAML import command so pipeline work can start before the web app exists. New open questions: should the digest and verdict buttons live in the web app too, how is it reached (localhost or Tailscale), and can one curator be pitched for two profiles.

## 2026-09-13: Half on Vercel, half at home

Jarred asked whether the whole thing could live on Vercel with a linked Neon database. The honest answer was half of it.

The web app is a natural fit. FastAPI runs on Vercel's Python runtime, Neon links in through the integration, and the app can be reached from anywhere without opening a port at home.

The pipeline is not. A Vercel function can run for 5 minutes on Hobby and 13 on Pro (30 in beta), and the Stage 0 quality check alone took 35 minutes. More important is where the traffic comes from. The spike succeeded from a home IP, while Spotify's page loads reCAPTCHA Enterprise, and cloud-server IPs are exactly what bot detection is tuned to distrust. Moving the fetch to Vercel would trade a free, proven setup for a fragile one.

So the system splits. Vercel serves the web app. The Mac Mini runs the pipeline under launchd and only ever reaches out, to Neon, Spotify and the APIs. Neon is the only thing they share. Three details make the split usable:
- **"Run now"** can't call the Mac Mini, so it writes a `run_requests` row that a small poller on the Mac Mini picks up within minutes.
- **A heartbeat** in the `runs` table lets the web app warn when the Mac Mini has gone quiet. Otherwise a switched-off machine would just look like a slow night.
- **Secrets stay where they're used.** Serper, Brave and Anthropic keys never go to Vercel. The web app gets its own Neon role, a pooled connection string, and a bundle with no Playwright in it.

Because the app is now on the public internet, Stage 2 gained real security criteria: session on every route, CSRF, rate-limited login, and tests that prove the door is locked. The remaining open question is how Jarred wants to log in.

## 2026-09-13: First push, first failed deploy

The repo went up at `github.com/burningpaper/noblehunter`. Before pushing we checked who could see it: it was public. That mattered because the first commit holds the Stage 0 spike, which shows exactly how to read Spotify's web player API logged out, along with the outreach strategy. Jarred decided to push while public anyway. That's his call to make, and it's recorded here.

The scaffold is deliberately tiny: a FastAPI placeholder in `web/app.py`, found through `[tool.vercel] entrypoint = "web.app:app"`, and a `/health` route that reports *whether* a database URL exists without ever echoing it. Playwright lives in a `pipeline` dependency group so it never reaches Vercel, and a check confirmed it isn't in the default install.

Vercel failed the first deploy within about a second: *the pattern "web/app.py" defined in `functions` doesn't match any Serverless Functions inside the `api` directory.* The `functions` block in `vercel.json` only understands `/api`-style functions, not an app found through the entrypoint setting. Its only purpose was keeping spike and test files out of the bundle, so we deleted it and moved that job to `.vercelignore`, which filters the upload before the build even starts.

Two access notes for next time. GitHub needed `gh auth login` plus `gh auth setup-git`, because the earlier "access check" only proved the repo was readable, which is true of any public repo. And Vercel build logs are behind Jarred's login: until the CLI is logged in with `npx vercel login`, diagnosing a failed deploy means asking him for the error text.

## 2026-09-13: Green, and completely empty

The second deploy came back **Ready**, and every route returned Vercel's own "page not found". That wasn't FastAPI's 404. It was the platform saying there was nothing there at all.

With the CLI now logged in, the evidence was quick to find. `vercel inspect` showed a 0 ms build. The build log showed `vercel build` finishing in 24 ms with no Python install and no function created. `vercel project inspect` gave the reason: **Framework Preset: Other.** The Vercel project had been imported at 19:37, before the first push, while the repo was still empty. With nothing to recognise, Vercel saved "Other", and that saved preset kept ignoring `tool.vercel.entrypoint` on every later deploy.

The fix is one line, but *where* it lives matters. Rather than flipping a dashboard toggle that nobody can see in the code, `vercel.json` now pins `"framework": "fastapi"`. That value was checked against Vercel's published schema first, since the previous `vercel.json` had broken the first deploy.

Lesson for next time: a green deploy is not a working deploy.

## 2026-09-13: Stage 1 foundations, and the role that would have been a superuser

Stage 1 is where the system's promises turn into things Postgres enforces. The headline is an EXCLUDE constraint on `outreach`: the same curator's `[digest_date, digest_date + 90)` ranges may not overlap, across every profile. The tests passed first time, which proves nothing on its own, so a mutation check dropped the constraint and tried again. With the constraint, a second entry within 90 days is refused. Without it, the entry goes straight in. The guarantee lives in the database, not just in hopeful Python.

Around it: contact normalisation (every spelling of a curator collapses to one key), settings that never echo a URL, a validated profile YAML format with a safe re-import, exclusion rules for Jarred's verdict decisions, and a least-privilege grants module. Everything was built test-first against a real Postgres 18 in Docker, once Docker Desktop got its admin password.

Two small traps along the way. A shell variable holding the list of test files didn't word-split in zsh, so pytest ran *nothing* while `| tail -1` hid the exit code. Now every chain uses `set -o pipefail` and explicit paths. And `normalize_text` had to move out of `profile_config.py`, because that module imports `yaml` and Vercel doesn't install it.

The bigger catch came from reading Neon's docs before creating the web and pipeline roles. **Roles made in the Neon console are added to `neon_superuser`**, which includes `pg_write_all_data`, `CREATEROLE` and `BYPASSRLS`. The plan said "create them in the console, then grant least privilege". That would have produced a public-facing web role able to write every table, with the grants as decoration. Roles created with SQL start with nothing. So the pipeline CLI creates them itself, with a generated password hashed to SCRAM on the client, so the plaintext never crosses the wire.

## 2026-09-13: Neon wants the password, not the hash

That last sentence didn't survive contact with Neon. Against local Postgres, `db create-roles` passed every test. Against Neon it failed with one unhelpful word: `InternalError`.

The first fix was to the diagnosis, not the code. Every `CREATE ROLE` variant succeeded on Neon inside a transaction: the exact statement, attributes only, hashed password, plaintext password. So did every GRANT, replayed one by one. Only the commit failed, and the real message was there all along: *Received HTTP code 400 from control plane: "Neon only supports being given plaintext passwords."* Neon syncs roles to its control plane when the transaction commits, and it refuses a pre-hashed SCRAM verifier.

Two changes came out of it:
- **Passwords are now sent as generated.** They're still 256 bits, URL-safe base64, over a TLS connection (`sslmode=require`), and Postgres stores them as SCRAM. A new test listens to the SQL actually sent. Local Postgres accepts hashes, so only that kind of test can pin the Neon requirement down.
- **CLI database errors now show the SQLSTATE and the first line of the server's message.** Password clauses, SCRAM verifiers, connection URLs and `npg_` strings are masked, and the SQL statement is never included. "InternalError" alone had hidden a one-line answer.

Nothing was left behind by the failed attempt: the transaction rolled back, the output file was never kept, and a throwaway probe role was created, confirmed and dropped.

## 2026-09-13: Stage 1 lands on Neon

With the password fix in, the real run was undramatic, which is the point. Before touching anything, a pre-check confirmed the target: a Neon host, the direct (not pooled) endpoint, and no leftover roles or credentials file. Migration `0001` then built all 15 tables, `btree_gist`, the 90-day EXCLUDE constraint and the three special indexes on the empty main database. `db create-roles` made `noble_web` and `noble_pipeline` and wrote their URLs to `.env.roles.local` with mode 600. No password appeared on screen.

Then came the part that counts: logging in *as* each role and trying things that should and shouldn't work, each attempt in a transaction that is rolled back. The web role read and inserted profiles, and was refused (`InsufficientPrivilege`) on writing playlists, touching migration history and creating tables. The pipeline role wrote playlists and was refused on migration history and schema. A GRANT statement only states intent. These attempts show what the roles can actually do.

Stage 1 closes with 196 tests. The one deliberate gap is the first real profile: Jarred would rather send Synman's actual reference artists and tracks than seed Neon with a placeholder.

Operational rules that come out of this stage:
- **Never create database roles in the Neon console.** Use `db create-roles`.
- **Re-run `db grant` after every migration.** New tables get no privileges until it runs.
- **`.env.roles.local` holds the only copy of the two role passwords.** If it's lost, drop and recreate the roles.

## 2026-09-13: Stage 2a, a web app that fails politely

Google sign-in details arrived in `.env.local`. They were checked for format without being printed, then piped into Vercel alongside `WEB_DATABASE_URL`, a freshly generated `SESSION_SECRET` and `ALLOWED_EMAILS`. Nothing secret ever reached the terminal. (One listing appeared to drop the Google variables. The cause was a regex, `GOOGLE_\s`, that could never match. It was worth checking before panicking.)

The foundation is built around one idea: on the public internet, the safe failure is a closed door with a sign on it. Every setting is required, so an app that doesn't know its allowed user refuses to run. If settings are missing, Vercel still gets an importable app. It answers `/health` with `configured: false` and shows a "not configured" page, instead of a stack trace nobody would see. Responses carry a strict CSP: scripts and styles come only from our own origin, which is why htmx 2.0.10 is stored in the repo (verified identical from two CDNs) and its injected inline styles are switched off. The engine keeps no client-side pool and turns off psycopg's automatic prepared statements, because Neon's PgBouncer hands each transaction to a different server connection.

The dark studio design is plain CSS on tokens: near-black ground, a single lime accent, a mono eyebrow, generous space, a short entrance motion, visible focus rings, and respect for reduced motion. Tailwind was dropped, because a CSS build on Vercel's Python builder was a moving part with no payoff at this size. Headless Chromium screenshots at 1280px and 390px confirmed it reads as intended. The console's only 404 was the deliberately missing test page.

## 2026-09-13: Stage 2b, a front door with one key

Sign-in is designed default-deny. One middleware sits in front of every route and lets through only `/health`, `/login`, the two OAuth routes and `/static`. Any page added later is protected without anyone remembering to protect it. A signed-out browser is sent to `/login?next=…`. An htmx request gets a 401 and an `HX-Redirect` instead, because redirecting a partial swap would paste a login page into the middle of a table.

Authlib does the cryptography: OAuth state, ID-token signature, nonce. Reading its installed source (the docs URL was dead) confirmed it keeps state in the session and puts verified claims in `token["userinfo"]`. Our callback holds the policy. The email must be *verified* and on `ALLOWED_EMAILS`. The session is cleared and rebuilt on sign-in, so a planted session can't carry over. `next` may only be a path on this site: `//evil`, `/\evil`, schemes and control characters all become `/`. Refusals render a 403 page. A broken exchange renders a 400 "didn't complete" page, never a traceback. Vercel's proxy can present the request as http, so the callback URI is forced to https in production, where Google insists on an exact match.

CSRF uses a per-session token that htmx sends as `X-CSRF-Token` from the page's `hx-headers`, compared in constant time for every unsafe method. One ordering bug was designed out before it shipped: security headers were the innermost middleware, so the guard's redirects would have gone out without a CSP. They are now outermost.

Unit tests use a fake Google. The proof that the parts fit came from a real browser: headless Chromium signed in through a local stand-in provider, landed home, clicked **Sign out**, and ended on `/login` at both 1280px and 390px, with zero console errors. A stranger's address landed on "This account is not allowed". That run exercises htmx, the CSRF header and the CSP together, which unit tests can't do.

## 2026-09-13 (overnight): Profiles you can actually shape, and two helpers working alongside

Jarred signed in for real, said "let's do both" (the profile editor and the pipeline), and went to bed. So the night split three ways. The profile editor stayed in the main checkout. Two helper agents each got their own git worktree: one for search (Serper and Brave), one for fetching from Spotify. The agent tool couldn't make worktrees itself, because the session had started before this folder was a git repository, so they were created by hand with `git worktree add`. Each helper was told to run only its own tests, since the rest of the suite rebuilds a shared local Postgres at start-up.

**Search came back first:** 53 network-free tests and a live check that found 59 unique playlists for "glitchy ambient" across both engines, with only 4 overlapping. It merged cleanly because it touched only its own two files. It also flagged a trap: Spotify's "The Sound of …" playlists slip past the editorial ID filter, so owner filtering has to happen at fetch time.

**Profiles (2c)** got their rules in `core/profiles.py`: names that are unique regardless of case, a digest target of 1–50, and activation that refuses and lists exactly what's missing, with counts so far. **Profile contents (2d)** added genres in priority order, reference artists, anti-signals, tracks and search terms, all editable in place. Two rules carry the most weight:
- **A search term that has already found playlists can be paused but never deleted**, so discovery history stays true.
- **An active profile that loses an essential ingredient pauses itself** and says so, rather than running half-configured.

Every edit returns its own section plus the status panel as an htmx out-of-band swap, so the readiness checklist, the Active/Paused badge and the "25 search requests per night" estimate stay honest without a reload.

Real browsers caught three things the tests couldn't:
- **htmx doesn't swap 4xx responses by default.** Without a `responseHandling` rule for 422, every validation message would have silently vanished.
- **Renaming a profile left the old name in the heading.** It's now fixed with out-of-band title and breadcrumb updates.
- **A scare that turned out to be the test tool.** A run logged inline-style CSP violations after each 422. Re-running with a `securitypolicyviolation` listener and no screenshots showed zero violations. Playwright's `page.screenshot` injects a `<style>` to hide the text cursor, and our CSP rightly blocks it. The app was clean all along. Browser checks now record problems before taking screenshots.

Browser checks now use their own `noble_browser` database in the same container, so they can never race pytest's schema rebuild. The full Stage 2d flow ran end to end with zero console errors. The check that counts is a real request to `/health`. Two more housekeeping notes. `vercel link` wrote a `.env.local` holding only a `VERCEL_OIDC_TOKEN`, which is gitignored. And the new `.env*` ignore rule needed a `!.env.example` exception so the template stays tracked.
