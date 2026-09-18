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

Browser checks now use their own `noble_browser` database in the same container, so they can never race pytest's schema rebuild. The full Stage 2d flow ran end to end with zero console errors.

## 2026-09-14 (small hours): Discover, and a fetcher that reads Spotify's mistakes

With the search layer merged, discovery got its database half. `pipeline/discover.py` takes each active search term, asks both engines, and decides per playlist. A never-seen playlist becomes a `candidate` with a tidied placeholder name, so "AMBIENT IDM 🤖 Braindance - playlist by Irles Music | Spotify" becomes the bit a human would call it. One that's excluded or already in flight is left alone. One whose re-check window has passed goes back into the queue. Every hit is still written to `playlist_sources`, even for playlists we skip, because the weekly "which terms actually work" report needs the whole picture. Sources insert with `ON CONFLICT DO NOTHING`, so re-running within one run can't double-count. Runs record their stage counts per profile, adding to one row rather than duplicating it.

The pipeline picks its database connection by least privilege: `PIPELINE_DATABASE_URL` (the `noble_pipeline` role from `.env.roles.local`) first, then the owner's direct URL, then `DATABASE_URL`.

The second helper finished the Spotify fetcher (`pipeline/spotify.py`): 96 unit tests, plus 4 live tests that fetched a real 127-track playlist with every date, a curator's 17 playlists, a large playlist read as first page plus tail, and a missing playlist that correctly came back as `not_found`. It also corrected my brief. I'd written that 127 tracks needs offsets 25 and 75, but page 75 ends at track 124, so the real plan is 25, 75, 125. The spike's `range(25, 127, 50)` had it right all along.

Its live runs found two things no fixture would have shown:
- **Missing playlists and users** never trigger the player's API call at all; Spotify just serves a 404 page. The client now checks the page status straight away instead of waiting out a 30-second timeout.
- **Spotify's web player sends Sentry telemetry** as newline-delimited JSON. Playwright's `post_data_json` throws its own error type on it, which escaped the request listener. The client now filters by URL first and decodes bodies itself.

Descriptions arrive with entities like `&amp;#x27;` and `&amp;lt;3`. Tags are stripped before unescaping, so a heart emoticon survives as `<3` rather than being eaten as a tag.

## 2026-09-14 (before dawn): The whole chain, run for real

With search, discovery, fetching and judging all in place, `pipeline/qualify.py` became the part with opinions. A playlist is alive if a track went in within 60 days and at least three in 180. It's real unless its description sells placements, or it has a vast following on a dozen tracks. It fits a profile by how many reference artists are already on it: three or more is top tier, one or two acceptable, and any anti-signal is a veto. "EDM" vetoes "EDM mix" but not "Edmonton". The verdict order is deliberate, because the recorded reason decides whether it's ever re-checked: Spotify-owned (new migration 0002), then pay-to-play (both permanent), then dead (re-checked in 90 days), then no fit.

`pipeline/evaluate.py` fetches candidates oldest first and judges each against every active profile in one pass. It stores one curator per Spotify owner and commits after every playlist. Failures follow their meaning: a missing playlist is dead, a timeout is retried next run, and being blocked stops everything at once. `pipeline.cli run` and `report` wrap it for a human.

Then it ran against the live internet, using a local database and the stand-in example profile. Five search terms found 274 playlists, 261 of them new. Fifteen fetches completed in three minutes, including one Spotify HTTP 500 that the retry quietly absorbed. **None qualified.** That's worth checking rather than celebrating or panicking, so every rejection was inspected. Nine were dead, several for three to five years, including an "IDM soft sound" playlist carrying four reference artists that nobody has touched since 2021. Six were alive but off-genre: jazz piano for sleep, Beatport's indie dance chart, a lo-fi label's dark ambient. The rules did their job; search results just skew old and adjacent, exactly as Stage 0 warned. A 150-playlist batch is running to see what the real yield looks like at scale.

## 2026-09-14 (dawn): Fifteen leads, and what they taught us about "qualified"

The batch took 21 minutes and Spotify never pushed back. Of 149 playlists fetched, **15 qualified (10%)**. The rest: 121 dead, 21 alive but off-genre, and 8 owned by Spotify itself, now caught by migration 0002. One playlist had vanished between search and fetch and was recorded as dead. The 10% is exactly the yield Stage 0 predicted from a hand-built sample, which is reassuring about the rules and sobering about search: most playlists search engines surface are abandoned.

The 15 leads are genuinely on-genre and alive: IDM, braindance and glitch playlists updated in the last 1–40 days, some carrying four or five of the stand-in reference artists. Read as *leads* rather than as test passes, they expose three gaps that no unit test could:

- **"Alive and fitting" isn't "a curator".** Most have under 100 followers, some fewer than ten. They're somebody's personal playlist, technically qualified and practically unpitchable. A minimum-followers floor is a judgement call for Jarred, not a constant to guess.
- **Some owners aren't people at all.** Chosic (a playlist generator), volt.fm (a listening-stats service), IDM Charts, and a label account all passed. They need the same treatment Spotify's own playlists got: an owner-level filter or a reachability tag, so they're kept out of the pitch list.
- **One curator, many playlists.** arcticdrones appears twice. The digest stage's one-pitch-per-curator rule will matter from day one.

All of this ran with stand-in reference artists. Synman's real list will reshape the fit results, but the dead, off-genre and non-human patterns will hold. The check that counts is a real request to `/health`. Two more housekeeping notes. `vercel link` wrote a `.env.local` holding only a `VERCEL_OIDC_TOKEN`, which is gitignored. And the new `.env*` ignore rule needed a `!.env.example` exception so the template stays tracked.

## 2026-09-14 (morning): A floor under "qualified"

Jarred settled the first gap in one line: under 50 followers isn't worth a pitch. He also wanted the number in the web app, per profile, not buried in code. So `profiles.min_followers` (migration 0003) defaults to 50, and the settings form edits it. It accepts any whole number from 0, meaning no floor, to a million.

The floor applies only after fit. A playlist that fits but is too small gets its own rejection, `too-small`, rather than being lumped in with `no-fit`. The difference matters in 90 days. Small playlists grow, so `too-small` is re-checked like `not-alive`. An anti-signal still vetoes outright, and if no profile fits at all the reason stays `no-fit`, because that is the truer story. Each profile uses its own floor, so a niche profile set to 10 can take a playlist that the main profile turns away.

Leaving the field blank keeps the current value. That way an older form, or a future caller that only renames a profile, can't quietly reset the floor to zero.

The second gap, owners who aren't people, gets no rule of its own. Jarred's view is that a lead with no reachable human is no lead. Stage 6's contact research will mark those `no-contact`, so Chosic and volt.fm drop out for the same reason as an anonymous personal playlist.

## 2026-09-14 (mid-morning): Reading the description first, and a schema that got ahead of itself

Contact research starts where the spec says the answers most often are: the curator's own description. `pipeline/contact_extract.py` pulls out emails (including `demos [at] label [dot] com`), handles, form links and Discord invites. It also lists the links worth following next. Its tests use formats copied from last night's real playlists, among them `Submissions: PlaylistsByElise@gmail.com`, `Follow @laguerradelasgalaxiasvinyl on Instagram` and `Submit tracks for consideration: https://discord.gg/...`. There are traps too: `record label @ www.site.org` is a website, not a handle, and `v0.2` is not a domain.

Order is the trick. Emails are found and blanked out first, so `gmail.com` never shows up as a site to visit. Web addresses come next, so `instagram.com/name` becomes a route and isn't read twice. Handles are last, and they only count when the text names the platform. A bare `@someone` could belong to anyone, anywhere. None of the 12 playlists that qualified last night had contact details in its description, so most of the yield will depend on the research agent, just as the spec warned.

Meanwhile production broke, predictably. Adding the Anthropic SDK changed `pyproject.toml`, and pushing that commit also pushed the unpushed minimum-followers change. Vercel deployed both, but Neon was still at migration 0001, so every Profiles page failed with `column profiles.min_followers does not exist`. Querying Neon as the web role showed the same error. The rule, now written down: **apply migrations to Neon before any push that changes the models.** A cheap future guard would have `/health` compare the database's revision with the code's head, so a gap like this shows up before anyone clicks.

## 2026-09-14 (afternoon): Asking Claude without leaving, and a runner that keeps its own hours

Jarred kept switching to Claude to brainstorm search terms, so the profile editor now has an **Ask Claude** panel in its genres, reference artists, anti-signals and search terms sections. He types a question and gets tick boxes back, each with a one-line reason. Claude sees what the profile already holds, so "more artists like these" works as expected. Anything already on the profile is filtered out in code rather than trusted to the prompt. Ticked items go in through the same validation as typed ones, and search terms are recorded as `suggested`. Each question is one small structured-output call with thinking off and low effort, costing about a cent.

The key had to reach Vercel for this. After the morning's outage, one rule stood firm: the web app still starts without `ANTHROPIC_API_KEY`, and the panel just says what's missing. A test also caught a subtle trap. The genre placeholder mentioned "IDM", and that broke an unrelated test checking that "IDM" had been removed. Profiles differ, so placeholders no longer name a genre.

Jarred's cost instinct also reset the budget. The plan had guessed $20–30 a night for contact research. Once free steps come first, only digest-bound playlists are researched and pages are trimmed, it comes out under $2. That's now the default cap.

Then the **runner**. This Mac is the Mac Mini, so it made sense as one long-lived process under launchd rather than two scheduled jobs. Once a minute it checks in to a one-row `worker_status` table (migration 0004). It then takes the oldest "Run now" request, or starts the nightly run once it's past 02:00 and no nightly run has started today. The same rule catches up when the Mac slept through 02:00. A Postgres advisory lock stops runs overlapping, and a background thread keeps checking in during an hour-long run. A restarted runner closes out anything a crash left "running". A run that can't even start, for example because Chromium won't launch, is recorded as failed, so the loop doesn't retry it every minute.

The web app shows all of this in a **status panel** on the Profiles page. It says whether the runner is online, what's running, when the last run finished, and when the next one is due. It warns plainly if the runner is quiet, the last run failed, or nothing has finished in 26 hours. Times are relative ("12 minutes ago"), so the Vercel server's time zone never matters. "Run now" is safe to double-click, because a partial unique index allows only one open request.

Building the panel exposed an old bug. `run_pipeline` stamped a run's finish time with its *start* time, because the same `now` anchors the liveness checks. The panel would have said "finished two hours ago" about a run that had just ended. Finish times now use real time.

## 2026-09-14 (afternoon, later): Finding the human, and handing Jarred his morning list

The spec's hardest stage is contact research, where the yield is lost. It's now a ladder that climbs only as far as it has to. The free steps come first. An email or handle in the curator's own description is grade A, because they chose it. The links in the description (a Linktree, a label site) come next, fetched within the playlist's budget of 10 fetches and 60 seconds. Claude only gets involved when both come up empty.

Fetching strangers' links from a Mac inside a home network needed care, so `pipeline/web.py` is suspicious by design. It fetches http(s) only, never a private, loopback or link-local address, checks again after every redirect, and limits size, time and content type. A page comes back as its visible text plus every link target, because a Linktree's buttons are nothing but links.

The agent gets three tools: a web search, a page reader, and `report_findings`, which ends the conversation. When fetches, time or turns run out, it's required to report, which is why thinking is off. Its answers aren't trusted, though. Every contact it claims is checked against the text its tools actually returned. An A must appear on the page it cites, and a B somewhere the agent really looked. Anything else becomes C and never reaches the digest. The first live run found a real curator's own Instagram bio and Linktree in 43 seconds for under five cents, and correctly marked a same-name SubmitHub page as a guess. One gotcha: when the money runs out or the API fails, a lead is left for another night rather than written off as "no contact". A shelved lead would otherwise be invisible for 90 days.

The digest ranks reachable curators across all profiles, gives each one to the profile they fit best, and asks Claude for an 80–120-word brief and an opening angle. If Claude fails, a plain template brief ships instead; a flaky API should never cost Jarred his morning. The live brief exposed a small prompt bug: Claude quoted a track's description as its title. Tracks are now labelled in the prompt.

Both stages plug into `run_pipeline` as plain callables after evaluation, so the nightly run and "Run now" do everything. The budget lives in a new `app_settings` table, edited on the Profiles page, and research reads it as it starts. A missing Anthropic key stops a run before Chromium launches, and the installer's check refuses to pass without it. A risk showed up in the CLI tests: `.env.local` now holds a real key, so the test fixture swaps the Claude stages for fakes, or a green test run would have spent money.

Finally, the page Jarred will actually use each morning is `/digest`. It groups entries by profile, in rank order, with the brief, the angle and the best contact as a link that can only ever be mailto or http(s). Pitched, skip, bad fit and dead swap the card in place through the same `record_verdict` the exclusion rules rely on. It reads only through `core`, so Vercel never loads pipeline code. Two test failures along the way were both the database being right. Profile names are unique, and so is an email address across curators, and my test helper had reused both.

Switching the live runner over to the new code turned up two restart problems worth remembering. First, a runner killed mid-run leaves a heartbeat only seconds old, so the 10-minute staleness rule would have shown that run as "running" indefinitely. Startup now checks the run lock first: if no runner holds it, nothing can really be running, so anything left open is closed at once (`46fc1d7`). Second, `launchctl bootout` returns before the old process has actually exited. A runner stopped mid-run needs about 30 seconds to close Chromium, so loading the new one straight away failed with "Bootstrap failed: 5: Input/output error". The install script now waits for the old job to disappear (`189b99a`). In practice the old runner recorded its own interrupted run as failed on the way out, and every playlist it had already judged stayed saved.

## 2026-09-14 (afternoon, last): Fitting without the reference artists

The first real digest had six entries, and the counts showed why it was short. Of 60 playlists checked, none qualified: 41 were dead, 2 belonged to Spotify, and 17 were active and real but had none of Jarred's reference artists on them. He called that rule "way too restrictive", and he was right. A playlist can be exactly the right room without happening to include Autechre.

Fit is now a ladder, like contact research. Reference artists still come first and count most. Next, a playlist qualifies if its own name or description names one of the profile's genres, matched as whole words, so "IDM" never hits inside "Humid". Only then, and only for playlists that are alive, real and free of anti-signals, Claude reads the most frequent artists, the name and the description against the profile, and says whether it's the same sound world. That check costs a fraction of a cent, counts toward the nightly budget, and research gets whatever is left. Anti-signals still veto everything.

Jarred wanted these new matches to rank below reference-artist matches, and it needed no new column. The digest already stores which reference artists are on each playlist, so any playlist with at least one of them sorts ahead; genre and Claude matches fill the rest in score order.

One test failure taught something small. A budget of $0.005 is saved as $0.01, because budgets are whole cents, so the test's "stop at the budget" case allowed a third check. The code was right and the test was asking for a budget that can't exist.

Because the rules changed, `pipeline.cli recheck --reason no-fit --days 7` puts the week's no-fit rejections straight back in the queue, rather than waiting 90 days for their re-check. Permanent rejections (pay-to-play, Spotify's own) are refused.

## 2026-09-15: Making room for more than one artist

Jarred wants to invite other artists, and a shared app would have meant a shared inbox once email pitching lands. So before any email work, Noble Hunter learned who owns what. An *artist* now owns profiles, and *members* are the Google accounts that work on one. Admins are still whoever is in `ALLOWED_EMAILS`, and they see everything. Migration 0006 moves Jarred's existing profiles under an artist called Synman; on an empty database it creates nothing, so a stray artist never shows up in a fresh test database. The migration can't create user rows, though, because `ALLOWED_EMAILS` lives in Vercel, not the database, so a user is written the first time someone actually signs in. A new People page lets admins add, rename and remove members instead of editing `ALLOWED_EMAILS` and redeploying, and Run now and the Claude budget became admin-only, because they spend everyone's money.

Stage 1 stops short of the goal, though, and that matters. It enforces sign-in and those admin-only actions, and re-checks access on every request rather than caching it in a cookie, so being removed from your last artist stops you on your next click. But nothing in stage 1 alone filters profiles, the digest or verdicts by artist: on its own it would have let any signed-in member see and edit every artist's work. That filtering is stage 2, and once a review pointed out the gap, Jarred chose to hold stage 1 back and ship the two together. So that window never existed in production.

Review before shipping caught three things that would have shipped otherwise. First, sign-in lowercased the incoming email before checking it for ASCII look-alikes, which defeats the point of that check: the Kelvin sign, written here as the escape `\u212a` (six ASCII characters in the source, one character once decoded), lowercases to a plain "k", so a crafted address could have matched a real member's email once both sides were folded to lowercase. The check now runs on the address as Google sent it. Second, the runner status panel was about to show every signed-in member the same raw pipeline error text admins see, and that text can name another artist's playlists or curators. Members now get one generic line instead. Third, the People page updated its results but left keyboard focus wherever it had been, so a screen-reader user got no indication that Add, Rename or Remove had done anything; results are now announced by moving focus to the new content or the first error.

One more change is structural rather than a feature: `pyproject.toml` now promotes every SQLAlchemy `SAWarning` to a test failure, not just a printed line. The prompt was a "cartesian product" warning: a query across artists, profiles and members that forgets a join doesn't just run slow, it can return every artist's rows instead of one, which is exactly the shape of bug this stage exists to prevent. Better to fail the test than leak the rows.

Stage 2 put every route behind the same question: is this on one of your artists? Profiles, profile editing, Ask Claude, the digest and verdicts all ask `core/access.py`, and anything on another artist answers 404, exactly like something that doesn't exist. The order is deliberate. Visibility is checked *before* a route validates what it was sent, so another artist's profile answers 404 even for a nonsense move direction or an Ask Claude section that doesn't exist. A 400 there would confirm the profile is real. If that ever looks like a bug worth "fixing" back to 400, it isn't.

The safety net is `tests/test_web_access.py`. Signed in as someone on a different artist, it calls every registered route with real ids from the other artist's data. It expects 404 for anything of theirs, 403 for admin-only actions, and 200 on the outsider's own pages, which must show the outsider's own profile and digest entry and nothing of the other artist's. It fails if a route is added without being listed in `tests/route_walk.py`, or if a mounted sub-app hides routes it can't see. Its sibling `test_web_access_sweeps.py` walks everything again as someone who was just taken off their only artist, then with ids too big for Postgres, spoiled all at once and one at a time, where the answer must be not found, never a crash. Reviews along the way found write routes that could answer 404 *after* they had already committed, so the walk doesn't trust status codes. Each write route gets a fresh world, seeded so the route would really change something (a profile is active before `pause`, and has two genres before `move`). The test then snapshots the other artist's data, plus the budget, the People lists and the run queue, and compares them afterwards. A second review caught that one combined walk could let a later leak undo an earlier one, which is why each route is checked on its own. `test_web_access_controls.py` is the other half of that check: an admin calling each write route with the same ids and form must change the snapshot, so no route can pass just because its call was a no-op. To prove it wasn't just passing, we deleted one `require_outreach` line and watched it fail. Then we moved one profile check to after the commit, so the route still said 404, and watched the snapshot catch the change the status code hid. One surprise: FastAPI 0.141 nests included routers, so the plan's "list `app.routes`" found just two routes. The walk flattens them with `iter_route_contexts` instead.

One known gap stays open until stage 3. A member's Bad fit or Dead verdict still sets `curators.excluded_at`, which hides that curator from every artist's digest and research, not just theirs. We accepted that rather than make those verdicts admin-only: it's rare while the 90-day window is still app-wide, and it leaks nothing. If a mark was wrong, clearing `curators.excluded_at` and `exclusion_reason` for that curator reverses it.

## 2026-09-16: Shipping stages 1 and 2

The release ran in the order the final review asked for, and the order mattered more than the code by then. The runner was stopped first, because it runs from the same checkout the merge was about to change: a crash-restart mid-migration would have loaded models describing a column that didn't exist yet, and a run holding a read lock on `profiles` would have made the migration wait while every web request queued behind it. Then main fast-forwarded, the migration ran with `PGOPTIONS="-c lock_timeout=5s"` so a lock fight would roll back cleanly instead of hanging the site, `db grant` re-ran for the three new tables and their sequences, and only then did the push reach Vercel. The runner went back on last.

Neon went 0005 to 0006 in one transaction: artist "Synman" created, the single profile moved under it, nobody's data touched. Afterwards the checks that mattered were the boring ones: the web role can insert into `users`, `artists` and `artist_members` and use their sequences, the site answers healthy, signed-out visits to `/people`, `/profiles` and `/digest` redirect to sign-in, and the runner checked in within seconds with tonight's run still scheduled for 02:00.

Two things about people, learned the hard way that morning. Adding someone to `ALLOWED_EMAILS` in Vercel does nothing until the next deployment, which is why a newly added address couldn't sign in; the ship's own push applied it. And in the new model `ALLOWED_EMAILS` no longer means "allowed in", it means **admin**: everyone in it sees every artist. Members belong to one artist and are added on the People page instead. Both of Jarred's addresses are admins by choice. Before anyone outside is invited, Ask Claude needs a per-member daily limit, since it spends the shared Anthropic key with no cap today.

## 2026-09-16 (through the night): Pitching by email

The digest used to stop exactly where the work started. It handed over twenty curators with a brief and an address, and Jarred went off to Gmail to write twenty emails by hand. Now the pitch happens where the lead is. Every digest entry carries a "Write a pitch" panel: Claude drafts the letter from the brief the entry already shows, Jarred rewrites it — he usually does, and the panel exists so that he can — and it goes out from the artist's own Gmail. Replies come back into the same panel, and a new `/inbox` page answers the question the digest never could. Not *who should I write to tonight*, but *who is waiting on me*.

The parts are layered so nothing above `core/` knows how Gmail works. Migration 0007 laid the tables: `mail_accounts`, `email_messages`, and mail columns on `profiles` and `outreach`. `core/gmail.py` is a five-call HTTP client whose errors carry a `kind` — `auth`, `transient`, `rejected`, `history-gone` — because three of Google's refusals mean three unrelated things to a person, and sorting them once here keeps four different recovery strategies out of every future caller. `core/mime.py` builds and parses the messages, and refuses any address or subject containing a newline, since a newline there ends the header and would let an address typed into a form smuggle a `Bcc:` onto every pitch. `core/mailboxes.py` owns connecting and letting go, `core/pitch_writer.py` asks Claude, `core/pitches.py` sends, `core/mail_sync.py` reads replies, `core/conversations.py` counts them. The web app (`web/mail.py`, `web/pitches.py`, the Inbox) is thin on top, and so is the runner: reading mail is a background thread on the Mac Mini ticking every five minutes, deliberately outside the run loop, because a nightly run can take an hour and a reply shouldn't have to wait for it.

Five decisions are the ones worth keeping.

**A mailbox belongs to an artist** — not to the app, and not to a profile. That is what lets a second artist join Noble Hunter without sharing Jarred's inbox, and what lets two of one artist's profiles pitch from the same address. Saying it in English is easy; making the database refuse the alternative is the interesting part. `mail_accounts` carries a redundant-looking unique key on `(id, artist_id)`, which exists purely so `profiles` can aim a *composite* foreign key at it. A profile pointed at another artist's mailbox is then rejected by Postgres itself, rather than by a check someone can forget to write in a route.

**Sending records the verdict through `record_verdict`,** the same call the Pitched button makes, rather than assigning a status of its own. There is one definition of a curator being used up for 90 days, and an emailed pitch has to mean precisely what a hand-recorded one means, or the exclusion rule quietly grows a second personality. The ordering inside `send_pitch` is load-bearing for the same reason: clean the text, refuse a ruled-out curator, resolve the address, call Gmail — and only then write anything. A failed send that had already written its row would claim we pitched a curator who never heard from us, burning them for 90 days over a 500 from Google.

**Only mail on a thread we started is ever stored.** The mailbox is Jarred's own Gmail: his bank, his family, the rest of his working life. So a message is matched to an outreach entry *before* it is fetched, never after. `core/mail_sync.py` turns our pitched threads into a map first, and a message whose thread isn't in it is dropped on the spot — never requested, never parsed, never stored. The test that pins this asserts the list of fetched messages is empty, and that empty list is the actual feature: an assertion on the database alone would have passed just as happily while the code read every message in the mailbox and threw most of them away.

**The digest works to a ceiling, not to a nightly number.** Jarred put his finger on it: "I'm less worried about writing 20 emails and more about having to sustain 20 conversations." Writing is the cheap part; the twenty half-finished threads are the expensive part. So `core/conversations.py` answers a different question — how many conversations are already open, and how much room is left under `open_conversation_limit` — and tonight's allowance is `min(digest_target, limit - open_now)`, floored at zero. A profile at its ceiling takes nothing tonight, and that is the system working. Nothing is stored: the answer is derived from the messages every time it is asked, so a reply landing at 3am changes tonight's allowance by itself. Three judgement calls shape the count. A conversation is an *emailed* pitch, because an entry marked Pitched by hand has nobody waiting on a reply. A pitch nobody answered within `quiet_after_days` is over in practice. And a curator who spoke last never goes quiet — it is your turn however long it sits there — *unless* the entry carries a verdict, which is what `CLOSED_STATUSES` is for: a "not for us" that you then mark bad-fit would otherwise hold a slot for ever, and twenty of those turn a ceiling of twenty into an allowance of zero, permanently, with nothing on screen to explain the empty digest. Research checks the allowance too, not just the digest. Researching one curator costs about five cents, so a full profile that still got researched would spend real money assembling leads the digest was never going to hand over.

**Google's consent screen is still an open question,** and it is the one thing that must be settled before any of this sends a real email. `gmail.send` is merely a *sensitive* scope, but `gmail.readonly` — which the reply sync needs — is a **restricted** one. The OAuth app is published, and publishing is not verification. Google may refuse the restricted scope outright, or show an unverified-app warning screen to whoever connects a mailbox. No amount of reading documentation settles it: it takes connecting one real mailbox and reading the screen Google actually shows. Until then the reply half of this feature is unproven outside our own tests, and which way it goes is not something to guess at here.

Three things a later reader should know, none of them broken today.

The two new conversation settings are editable in the web app but **cannot be set from YAML at all**. The premise elsewhere is that the web form, the importer and any future caller all reach a profile's settings through one validator; the form does, and `core/profile_import.py` does not — it assigns `digest_target` straight onto the model and never calls the validator. Nothing is wrong today, because `ProfileConfig` validates the file on the way in and YAML is only ever a bootstrap and backup tool. It is recorded because the obvious assumption is that the validator is the single way in, and it isn't.

If the digest runs **twice in one day** and entries from the first run are emailed in between, those entries count both in `taken` and in `open_now`, so the evening top-up is doubly conservative. It errs toward handing over fewer leads, which is the safe direction, but it is not arithmetic anybody designed.

`pitch_address` falls back to any email contact ordered by confidence when the curator's best contact isn't an email, and that fallback has **no grade filter**. A curator whose only address is grade C — which the research prompt defines as "only the name matches", an address nobody confirmed belongs to this person — is therefore pitchable, and that is a real email to a possibly uninvolved stranger. The compose panel shows the address in bold immediately above Send and asks for confirmation naming it, so it is at least visible before anything goes out; it is not yet *labelled* as uncorroborated. Showing the grade next to the address belongs with the next piece of contact work.

The suite is 1757 passed, 6 skipped, and lint is clean. Migration 0007 is written but has **not** been applied to Neon, and nothing has been pushed.

## 2026-09-16 (later): The letter that signed itself "burningpaper"

Jarred signs in as burningpaper@gmail.com. The pitch route took the part before the @ and handed it to Claude as the name to sign off with, so every draft closed "burningpaper" — and so did the plain fallback draft, which is the one used precisely when Claude has already failed and the text is least supervised. A real curator was going to read it.

The fix is small and the idea behind it is the point. `web/pitches.py` now looks up the signed-in person's `users.name` and passes that, or `None` when Google never sent one. There is deliberately **no** fallback to the address: with nothing to sign, `core/pitch_writer.py` signs as the artist instead, and "I make music as Synman", signed Synman, is a true thing for a musician's cold pitch to say. An email fragment never was. `PitchRequest.sender_name` is now typed `str | None`, which it had always been in practice — two existing tests were already passing `None` and `""` through it.

The interesting part is why nothing caught this. `tests/test_pitch_writer.py` built its own `PitchRequest` with `sender_name="Jarred"` — a plausible human name, and therefore precisely the value the route never produced. `tests/test_web_pitches.py` drove the real route with a fake writer that records every call it gets, but only ever asserted on the playlist name and the instruction. Both sides were green, and neither had ever met the other. The new tests close that seam by asserting on the request the *route* built: a name reaches the writer, a missing name arrives as `None` and signs as the artist, and the string "burningpaper" appears nowhere in what the writer was handed nor in a fallback draft made from it. The lesson worth keeping: a fake that records its calls is only worth the assertions you make about what it recorded.

The suite is 1760 passed, 6 skipped, and lint is clean.

## 2026-09-16 (final review): Three ways a bad address got out

The final review of the email-pitching branch turned up three defects that looked unrelated and weren't. Each is a way for a malformed email address to reach a real curator, or to crash the route that sends it.

**The address we emailed was never the address we normalized.** `pipeline/research.py` stores a scraped contact twice over: `contact_key` goes through `normalize_email`, and `value` is kept exactly as it came off the page. The key decides whether two spellings are the same curator, and it is careful. The value is what goes in the To header, and it is whatever the page happened to say — `<nina@b.com>`, `Nina@B.com.`, `mailto:nina@b.com`. So the deduplication was right and the envelope was wrong: an address we had correctly recognised as Nina's was then emailed verbatim, to bounce, or to render as nonsense in her mail client. `pitch_address` now normalizes at both of its return paths, using the same function that settled the identity in the first place — which is the actual point. The address we write to and the identity we deduplicate on can no longer disagree, because there is one rule and both of them ask it.

That carries a deliberate cost, written into the code so nobody quietly undoes it. `normalize_email` lowercases the whole address, local part included, and RFC-wise a local part may be case-sensitive: in theory `Nina@` and `nina@` are two different mailboxes. In practice no mainstream provider treats them as two, and the app has already made that bet — it is how curator identity is decided everywhere else. Matching the bet we already placed beats inventing a second rule that contradicts the first.

**A `HeaderProblem` left the send route as a 500.** `core/mime.py` refuses a header that is empty or holds a line break, which is right and is what stops a form field smuggling a `Bcc:` onto a pitch. `web/pitches.py` caught four kinds of failure and turned each into a sentence in the panel — its own comment says "a 500 is exactly what this must not be" — and did not catch that one. It does now: rolls back like its neighbours, and says an address on file isn't usable, deliberately without claiming which end. Both the mailbox and the curator's contact are "an address on file", and the person reading it can check both.

**An empty Gmail address could build a broken mailbox.** `Gmail.profile()` read `emailAddress` with a default of `""` and no guard, unlike the explicit refresh-token guard next door in `web/mail.py`. On a surprise payload that `""` flowed into `connect_mailbox`, which stored a mailbox with no address — and then every send from it built an empty From header, raised `HeaderProblem`, and produced exactly the 500 above. Three defects, one failure, arriving in sequence. A profile that names no mailbox is now a `GmailError` of kind `rejected`, the same shape the module already uses for a send that comes back without a message id.

The tests came first and each was watched failing for its own reason, which mattered more here than usual. The curator-address test failed by returning **200** — the route cheerfully sent `Nina <nina@broken-machines.com>` to a curator — while the mailbox test failed by raising `HeaderProblem: From is empty` straight through the route. Two unrelated wrongs that a single "it isn't 422" assertion would have blurred into one. The send tests assert on the address that reached the fake Gmail rather than on a string built in the test, because the thing worth pinning is what a curator's mail server would actually see.

One honest note about the refusal path. A stored value that cannot be parsed at all should not be reachable today: research derives the key from the value and drops the contact when that fails, so a row with a good key and an unusable value is not something the pipeline writes. The guard is there because nothing keeps the pair agreeing *after* the row is written, and the failure it prevents — an arbitrary string in a To header — is a real email going somewhere nobody chose. The test builds that row deliberately rather than pretending the pipeline produces it.

The suite is 1772 passed, 6 skipped, and lint is clean. Still nothing pushed, and migration 0007 still hasn't been applied to Neon.

## 2026-09-16 (live setup): The 403 that meant the opposite of what we said

The first real mailbox refused to connect. `GmailError: Gmail said 403 to profile`, and underneath it the app's advice: reconnect the mailbox. The advice was wrong in the most expensive way available, because the Gmail API had never been enabled in the Google Cloud project at all. The very first call to `gmail.googleapis.com` was refused by Google before it ever reached a mailbox. No amount of clicking Connect Gmail could have fixed that, and clicking Connect Gmail was precisely what the app asked for.

The bug was one line of classification. `_kind` treated every 401 and 403 as `auth` unless the body named a rate limit — a rule written with one true case in mind, a revoked token, and blind to the several other things Google says 403 about. Three of them matter here and they pull in different directions: `accessNotConfigured` / `SERVICE_DISABLED` means the API is switched off for the project; `insufficientPermissions` / `ACCESS_TOKEN_SCOPE_INSUFFICIENT` means the token was issued without a scope the call needs; a bare 403 with nothing useful in it really is a bad token. Only the last one is about the mailbox.

What made the misreading costly rather than merely annoying is the flag on the other side of it. `auth` calls `mark_needs_reconnect`, and nothing in the codebase clears `needs_reconnect` except a person completing Google's consent flow — `connect_mailbox` sets it false, and that is the only route back. So a misclassified configuration error did two things at once: it gave advice that could not work, and it left a sticky flag on a mailbox that had never been broken, excluding it from `_connected_mailboxes` until someone went through consent for no reason.

So 403 now has a third answer. `config` sits between the rate-limit check and the general 401/403, and the order is the whole design: a busy mailbox must never read as a broken project, and a broken project must never read as a bad token. The two 403 tests — `_is_rate_limit` and the new `_is_config` — now share `_reasons`, which reads `error.errors[].reason` and `error.status` case-insensitively and returns an empty set for a body it can't parse. That last part is deliberate: an unreadable 403 names no reason, so it stays `auth`, exactly as before.

The message changed too, and that is the part a person actually meets. "Gmail said 403 to profile" is true and useless. A config error now says the Gmail API isn't enabled for this app's Google Cloud project, or that the access is missing a scope, and in both cases says that reconnecting won't help. Nothing else about the module's discipline moved: no token, no address, no message body in any sentence it produces.

Three call sites had to learn the new kind or it would have fallen through as "something else". `web/pitches.py` says nothing was sent and that this is a setup problem with the app's Google project, then appends Gmail's own sentence about which setting is wrong. `core/mail_sync.py` stops the round on a config error rather than counting the mailbox as ignored — every later call would fail the same way, and a round of quiet ignores reports as clean — and records `last_error` on the row **without** raising `needs_reconnect`. That asymmetry needed one more change to be honest: the success path used to clear `last_error` only when the flag was set, so a config sentence would have outlived the problem forever. It now clears whenever either is set.

A fourth place was worth checking and turned out not to exist. `web/inbox.py` and `pipeline/mail.py` both branch on `outcome.error` being truthy rather than on `kind`, so a config error already surfaces in both without a new case. One wrinkle to know about: the Inbox's "Check now" notice says "Some mailboxes couldn't be read; the runner will try again", which is a slightly optimistic thing to say about a disabled API. It is a single notice covering every mailbox in one pass, so making it kind-aware is a bigger change than this fix, and it is left alone on purpose.

The tests were written first and each was watched failing for its own reason, which was worth the trouble: three failed on `assert 'auth' == 'config'`, one on a `last_error` that was never recorded, one on a round that reported itself clean while ignoring the only message it had, and one on a panel offering "Try again shortly". The regression that actually cost time — a config error leaving `needs_reconnect` alone while an auth error still sets it — is now stated outright in `tests/test_mail_sync.py`, because that is the assertion whose absence let the whole thing happen.

The suite is 1784 passed, 6 skipped, and lint is clean. Nothing pushed, and migration 0007 still hasn't been applied to Neon.

## 2026-09-17: The LaTeX that nearly went to a curator

A real pitch, one keystroke from a real curator, had this subject line: `Human Error cdot for Neoclassical Music Gems`. It was caught by a person squinting at a phone screenshot, which is not a safety net so much as a piece of luck.

`cdot` is the LaTeX command for a centred dot. Nobody asked for LaTeX. The prompt asked for "a subject line under 80 characters that names the track and the playlist" and then said nothing whatsoever about how to *join* those two things, so the model did what a writer does when handed two nouns and no conjunction: it reached for a separator. It reached for a typographic one, and what came out was the command name with its backslash missing.

The missing backslash was worth pinning down before touching anything, because the obvious suspect is the wrong one. `_parse` uses strict `json.loads`, and strict JSON has no `\c` escape — an unescaped `\cdot` in the model's output is a parse error, which becomes a `PitchWriterError` and a draft that never appears. So the backslash was almost certainly never there. The three cases were checked directly: a literal `\cdot` is rejected outright, an escaped `\\cdot` parses into a string carrying a real backslash, and the bare word sails through untouched. The parsing is behaving correctly and was left alone. Only the third case is what the curator nearly saw.

The fix has two halves, because a prompt instruction is a request and the guard is the guarantee.

The request: the subject sentence in `SYSTEM_PROMPT` now says ordinary words and ordinary punctuation, no LaTeX, no markdown, no typographic control sequences, nothing exotic standing in as a separator. One clause, in the same plain voice as the rest of that prompt, which is written to a writer rather than to a machine.

The guarantee: `_clean_subject`, applied in `_parse` after the existing whitespace collapse. Anything shaped like a backslash command goes; a lone backslash before punctuation loses the backslash and keeps the punctuation, so `\&` reads as `&`; and a short denylist of bare command names — `cdot`, `bullet`, `textbar`, `ndash`, `mdash`, `textbullet`, `quad`, `hspace` — is removed as whole words, with the gap closed up so `Human Error cdot for X` becomes `Human Error for X` and not `Human Error  for X`.

One decision in there is worth writing down, because it is a deliberate departure from how the fix was first specified. The bare words are matched **case-sensitively, lowercase only**. Matching them case-insensitively, as originally asked, cannot survive its own acceptance test: `bullet` case-insensitively eats the first word of `Bullet Train for Broken Machines`, and a pitch for a track called `Quad` arrives with no track in the subject at all. Both were checked against a naive case-insensitive pattern before choosing, and both come out mangled. Lowercase-only splits them cleanly, on a real signal rather than a lucky one: these are LaTeX command names, a model drops them in lowercase, and a title capitalises. The residue is narrow and known — a legitimately lowercase "bullet" or "quad" mid-subject would still be taken — and that is the trade accepted, because a subject that loses the track's name is a worse email than one carrying a stray word.

The body is deliberately not sanitised, and the comment in the code says so. It is long-form prose the musician reads and edits before anything is sent, so a stray word there is visible and harmless. Silently deleting things from someone's draft is the worse failure, and it is the kind that erodes trust in the whole box.

The tests came first and each was watched failing for its own reason: the bare word surviving, the real backslash surviving, and the artifact removal leaving a doubled space. The three remaining tests pass before the fix and always did — the two survival tests and the untouched body — and that is the point of them. They guard the fix rather than the bug, and they were confirmed meaningful by checking them against the naive pattern the guard deliberately isn't.

The suite is 1790 passed, 6 skipped, and lint is clean. Nothing pushed, and migration 0007 still hasn't been applied to Neon.

## 2026-09-17 (later the same day): The guard was standing in the wrong doorway

The LaTeX fix worked exactly where it was put, and that turned out to be the problem. `_clean_subject` was applied in `_parse` — the moment Claude's draft is *created*. Sound instinct, wrong doorway. A draft is not sent the moment it is written: `web/pitches.py` stores the model's subject in `outreach.draft_subject`, the panel re-renders it into the box, and pressing "Draft it" and then "Send" without touching a key is not an edge case, it is how the app is used.

So a draft written the day before the guard existed sails straight past it. This is not a thought experiment. The production database held exactly one stored draft at the time of writing, and it was the bad one: outreach 29, `Human Error cdot for Neoclassical Music Gems`. The fix that was shipped to prevent that subject would not have caught that subject. A guard that cannot catch the one live instance of the thing it guards against is a guard in name only.

The last thing to touch a subject before the message is built is `_clean` in `core/pitches.py`, and until now it collapsed whitespace and nothing else. That is where the cleaning belongs, because that is where the guarantee can actually be made.

Before moving it, the send path was read end to end to find out whether there was a third way around — the previous round found this hole by reading carefully, and that turned out to matter more than the fix it was reporting on. There isn't one, and the shape of the code is what makes that answerable rather than hopeful. `build_message` has exactly one caller in the entire repository, `core/pitches.py:127`. `gmail.send` has exactly one, twelve lines below it. Both sit inside `send_pitch`, and `send_pitch` calls `_clean` as its first statement, before the duplicate-send check and before the address lookup. The only other caller of `_clean` is `save_draft`. Nothing in `pipeline/` imports `core.pitches` at all, and the incoming half of the mailbox — `parse_message`, `core/mail_sync.py` — only ever writes messages *received*, never sent. One funnel, one gate, and the gate is now on it.

Two small decisions inside the change are worth recording.

The helper was **renamed** from `_clean_subject` to `clean_subject` rather than imported through its underscore. The underscore was honest when one module used it; the moment a second module imports a name, that name is part of the first module's interface, and a leading underscore saying otherwise is just a lie that survives into every future reader's head. The rename cost two lines, because no test referenced it by name — the existing tests exercise it through `_parse`, which is the right way round. What was explicitly *not* done is the tempting thing: a second copy of the regexes next to the code that needed them. Two denylists drifting apart is a worse bug than the one being fixed, so there is one list and it lives in `core/pitch_writer.py`. `core/` importing `core/` is fine, and the architecture test only forbids `core/` and `web/` reaching into `pipeline/`.

The second decision was a trap rather than a choice. `_clean` already had a local variable called `clean_subject`. Importing a function of the same name would have made every reference in that function local, and the first line would have died with `UnboundLocalError` — a shadowing bug shipped inside a bug fix. The locals are now `subject_line` and `body_text`.

Order is the whole design of the new `_clean`: clean, collapse, truncate to `MAX_SUBJECT_LENGTH`, *then* check for emptiness. Cleaning can empty a subject — a subject of nothing but `cdot` cleans down to nothing at all — and the old order would have found it non-empty, cleaned it to a blank string, and put an empty Subject header in front of a curator. Now it fails closed on the message that already existed for the blank case: "Give the email a subject."

And then the judgement call, which is the part of this worth arguing with. Cleaning at send time means altering a line a human being may have typed with their own hands, which is meaningfully more intrusive than tidying a machine's draft. The verdict is that it is still right, for three reasons that only hold together. The words removed are LaTeX command names, matched in lowercase and as whole words only, so a real title keeps every word of its own — `Bullet Train for Broken Machines` goes out untouched. The subject is the one line that decides whether a cold email is opened, and the one line nobody rereads. And the cleaned text is not smuggled anywhere: it goes straight back into the box the person is looking at, where it can be seen and changed. Take away any one of those — match case-insensitively, hide the result, or apply it to prose — and the trade stops being worth it. Which is exactly why the body is still not touched. It is long-form writing the musician reads and edits before anything is sent, so a stray word there is visible and harmless, and silently deleting from someone's writing is the worse failure.

Four tests, written first, asserting on the message that actually reached the fake Gmail rather than on a return value agreeing with itself. Two of them failed, each for its own reason: one on `Subject: Human Error cdot for Neoclassical Music Gems` arriving intact in the built message, the other on a plain `DID NOT RAISE PitchProblem` for the artifact-only subject — it would have sent. The other two pass before and after, by construction, because before the fix nothing altered anything. They guard the fix rather than the bug, and rather than trust them they were checked against the naive case-insensitive pattern the guard deliberately isn't: it turns `Bullet Train for Broken Machines` into `Train for Broken Machines`. They fail when they should.

The suite is 1794 passed, 6 skipped, and lint is clean. Nothing pushed, and migration 0007 still hasn't been applied to Neon.

## 2026-09-17 (later still): The clock that was wrong by exactly one offset

A pitch went out at 11:53 and the thread said 09:53. Nothing was wrong in the database. `sent_at` is `DateTime(timezone=True)`, the instant stored was correct when it was written and is correct now, and the storage never needed touching. The whole defect was one line of display: `web/templates/pitch/_panel.html` called `strftime("%-d %b, %H:%M")` on a timezone-aware UTC datetime, and `strftime` does not convert. It reads the numbers off the object and prints them. A UTC instant formatted that way comes out as though UTC were the reader's own wall clock.

That makes this a particular species of bug, and the species is what makes it worth writing down. It is wrong by exactly the reader's offset, which means it is always *plausible*. Two hours out in Johannesburg still looks like a time. Nothing about it invites a second look, so it survives every glance it gets — and for a member invited from another country it would be wrong by a different amount, just as quietly. The failure mode isn't "the page looks broken", it's "the page is believed".

The server cannot fix this by itself, and that constraint decided the whole design. Vercel runs in UTC, so the deployment has no local zone to fall back on. Deriving one from the artist was the tempting shortcut and it fails in precisely the original way: it holds until someone travels or a member is invited from abroad, and then it is wrong, silently and believably. The only party that actually knows the reader's zone is the reader's browser. So the server's job changed from *formatting a time* to *naming a moment* — `<time datetime="{{ message.sent_at.isoformat() }}">` — and the formatting moved to the one place with the information to do it, `web/static/js/localtime.js`.

Which leaves the question of what the element says before any script runs, and the answer is the part worth arguing about. The obvious move is to leave the old text as the fallback. That is wrong, because the old text *is* the bug. A fallback that is merely present is worth nothing if it is untrue; if the script never runs — JavaScript off, a script error, a text-mode browser — what remains has to be accurate. So it renders as `17 Sep, 09:53 UTC`. Plainer than before, and honest, which is the trade: an unlabelled wrong time is what we came here to remove, and a labelled right one is only boring.

Making that label true needed four lines rather than none, and the reason is subtle enough to have been nearly missed. Postgres returns a `timestamptz` in the *connection's* zone — always the right instant, but not always wearing a UTC offset. Formatting it as-is under a "UTC" label would have printed a local clock reading beneath a claim that it wasn't one: the same lie in better clothes. `utc_clock` in `web/templating.py` converts first, and it has its own test that passes a `+02:00` datetime and demands `17 Sep, 09:53` back — the one case the end-to-end tests can't reach, because the test database answers in UTC.

The trap in this change was never the timezone maths. It was htmx. That panel is loaded by `hx-get` when someone expands a `<details>`, and **sending swaps it again out of band**. A script hooked only to `DOMContentLoaded` would format the first render and leave every swapped-in timestamp raw — the bug fixed on Tuesday and quietly back the moment anyone sends a pitch, which is exactly the moment nobody re-checks. So the script hooks `htmx:load` and `htmx:afterSettle` as well, and both run the same sweep of the whole document. That overlap is deliberate and it is free, because each element is formatted from its `datetime` attribute and never from its own text. Idempotence is what buys the belt-and-braces: `htmx:load` covers each newly inserted fragment, and `afterSettle`'s full-document sweep catches anything an out-of-band swap put somewhere else on the page, so the correctness of the out-of-band path doesn't depend on getting htmx's event semantics exactly right from memory.

It lives in `base.html` beside htmx rather than on one page, because the panel appears on both the digest and the Inbox and any page may render a `<time>`. It is a file rather than an inline script because the CSP is `script-src 'self'`, which an inline script would violate — the same reason htmx itself is vendored. No new dependency, no CDN, consistent with the rest.

Two tests came first and were watched failing for their own reasons: no `<time>` carrying the instant, and no UTC-labelled text. The first compares parsed *instants* rather than strings, because what the attribute must get right is the moment, not which offset it wears while naming it. The JavaScript has no browser harness and is not covered by the suite — that is a real gap and it is stated rather than papered over. What was done instead: `node --check` for syntax, and a stub-DOM harness that fired all three hooks and asserted the four things worth asserting — that a real instant gets rewritten, that both swap hooks format content too, that running twice changes nothing, and that an unparseable value leaves the honest fallback alone instead of writing "Invalid Date" into the page. Run on this machine, which is SAST, a `09:53Z` instant came back as `Sep 17, 11:53`. That is the original bug reproduced in reverse and corrected, which is the strongest evidence available without a browser.

Last, the audit, since one instance of a bug like this implies others. Every other datetime on the site goes through `relative_time` — "12 minutes ago", "in 5 hours" — which is a difference between two datetimes and therefore immune by construction, whatever zone anything is in. The digest's dates are `date` objects with no clock and no zone to get wrong. `core/inbox_view.py` does expose `last_at`, and it was the one real candidate; the Inbox renders only `days_since_last`, an integer, so it never prints a clock. The panel was the only place.

The suite is 1797 passed, 6 skipped, and lint is clean. Nothing pushed, and migration 0007 still hasn't been applied to Neon.

## 2026-09-17 (last): The missing icon that was reported as a broken login

A user said he couldn't sign in. He was signed in perfectly well. What he was actually looking at was `noblehunter.vercel.app/favicon.ico` in his address bar and the full styled "Page not found" page underneath it — because the app had no icon, and on Vercel every path routes to FastAPI. The browser asked for a file nobody had put there, the catch-all did what it does with an unknown path, and the result was an error page wearing the app's own clothes. That is the part worth naming: a 404 that is *well designed* reads as a locked door rather than a missing file. It also meant every first-time visitor spent a serverless invocation rendering an error page for an icon.

The fix is three small pieces, and what's interesting about each is the constraint that chose it.

The icon is a hand-written SVG at `web/static/icon.svg`: a viewBox, one circle, nothing else. It is the dot that sits beside the wordmark in the nav, standing on its own. Its colour was *read* from `--color-accent` in `css/app.css` (`#c6f36b`) rather than eyeballed off a screenshot, because that token is what `.wordmark__mark` is painted with, and an icon that is nearly the brand colour is worse than one that is obviously wrong. A static file can't read a CSS variable, so the value is necessarily duplicated — and the honest thing to do with a duplicated constant is say so out loud. There's a comment in the SVG naming the token, and a test that re-reads `--color-accent` out of the stylesheet and asserts the icon still carries it. Change the token alone and the suite objects. The circle is r=15 in a 32 box, close to the edges on purpose: a dot floating in a wide margin dissolves at 16px, which is the only size that really matters.

`base.html` declares it twice, as `rel="icon"` and as `apple-touch-icon`. The second isn't decoration. Both people using this thing are on phones, and without it a home-screen bookmark falls back to a screenshot of whatever page happened to be open.

Then the legacy path. Declaring the link tag stops most modern browsers asking for `/favicon.ico` — but not all of them, and not a direct visit, which is exactly what the user had done. So `/favicon.ico` is now a route beside `/health`, answering 204 with an empty body. 204 rather than serving something, because the icon is already declared as SVG and there is no `.ico` to serve. 204 rather than a plain 404, because a 404 is precisely what rendered the error page in the first place. No person ever reads this response, so the right answer is the quietest true one.

Two traps sat in this, and the tests found both rather than careful reading.

The first was the guard. `web/guard.py` is default-deny — every path needs a session unless it's on the public list — so the shiny new route dutifully redirected the browser's icon request to the sign-in page. A redirect is not an icon, and it's the same shape of bug as the one being fixed: the app answering a machine's question with a page meant for a person. `/favicon.ico` joined `/health` and the sign-in paths in `PUBLIC_PATHS`, because a browser asks for it before anyone has signed in.

The second was the access walk, behaving exactly as designed. `tests/route_walk.py` asserts every registered route is accounted for, so adding one without listing it failed `test_every_route_is_covered` with `Extra items in the left set: ('GET', '/favicon.ico')`. It went into `EXEMPT` beside `/health` — the list for routes usable without access to any artist — and deliberately not into `ROUTES`, because there is nothing per-artist here to get wrong. The point of that assertion is that access can't be forgotten quietly, and it wasn't.

The CSP was checked rather than assumed, and needed nothing: `img-src 'self' data: ...` already covers a same-origin SVG. That check is now a test, which is the difference between "it happens to work" and "it will keep working" — because the failure mode of losing `'self'` from `img-src` is an icon that silently never appears, with no error anywhere for anyone to notice.

Six tests, written first and watched failing: five for the right reasons (404s, and a missing link tag), and the CSP one green from the very start. That last one is the honest result of confirming a policy that was already correct, not a test bent into agreeing with itself.

The suite is 1803 passed, 6 skipped, and lint is clean. Nothing pushed, and migration 0007 still hasn't been applied to Neon.

## 2026-09-18: Sixteen entries that weren't yours

Last night's digest opened with sixteen NEWIRE entries, and Synman's underneath them. Nothing was broken. There are two artists now, an admin sees every artist by design, and "every artist" had quietly turned the page from a list you work down into one you scroll past. This adds `?profile=<id>` to the digest, and the most interesting thing about it is how little of it is new.

The Inbox solved this exact problem a week ago. So the first decision was to invent nothing: a query parameter, `form_id` to parse it, `require_profile` to refuse another artist's, a row of small buttons rendered only when there's a choice to make, `aria-current` on the active one. The temptation with the second instance of a pattern is to improve it slightly, and that is precisely how a codebase acquires two ways to filter by profile that differ in some detail nobody can reconstruct the reason for. This follows `web/inbox.py` down to the attribute order in the anchor tags. `digest_view` takes a keyword-only `profile_id` and exposes `filter_profiles` and `chosen_profile_id` the way `InboxView` exposes `profiles` and `chosen_profile_id` — a different field name only because `DigestView.profiles` was already taken by the entry groups.

It filters by profile rather than by artist, which today is a distinction without a difference — each artist has exactly one profile, so the two lists are identical. It won't stay that way: a second profile per release is expected, and the digest is already grouped by profile. Filtering by artist would work perfectly right up until the week it silently stopped being the thing anyone wanted.

Two things have no Inbox equivalent, and both are the species of half-finish that never shows up in a test you thought to write, only in use.

The first is the date navigation. The digest steps back through nights; the Inbox doesn't step anywhere. A filter that the earlier/later links drop is worse than no filter, because the page doesn't announce it — you narrow to one artist, click back one night, and everything you filtered out is there again with no explanation. The reader's conclusion isn't "the link dropped my filter", it's "this page is confused". So `filter_query` is a property on the view, and every link that stays on the digest appends it. A property rather than a conditional repeated in the markup, because the failure mode is forgetting it on exactly one of the links, and one place to forget is better than three.

The second is subtler and worth the most thought: `digest_view` built its list of dates from every entry the viewer could see. Filtered to NEWIRE, that list still offered nights when only Synman ran — so stepping back landed you on an empty page for a reason the page couldn't explain. The dates now follow the filter, which drags "latest" along with it: with no date in the URL, the digest opens on the chosen profile's own most recent night, not the world's. That is the answer that keeps the promise the filter makes. Ask for NEWIRE and you get NEWIRE's last night, not a blank page proving NEWIRE didn't run last night.

Which leaves the case that survives all of that: a date in the URL that this profile genuinely missed. The old copy said "Nothing on this night", and under a filter that is technically true and reads like a bug, because the reader knows perfectly well something ran. It now says "Nothing for this profile on this night" and offers a link — "Show every profile" for that same night. The unfiltered wording is untouched, deliberately: `test_a_night_with_only_others_entries_looks_like_a_night_with_none_at_all` compares two whole rendered pages to prove a member can't tell an empty night from someone else's night, and that page had to stay byte-identical to itself.

The chips make the same promise in the other direction. Each one keeps the night you're on, so filtering doesn't also move you in time, and the list is drawn from every profile with entries on *any* night rather than tonight's. Scoping it to tonight would have made the row flicker as you navigated, and — worse — made the chip you're currently filtered to vanish on exactly the night where you most need the way back.

The CSS was the one place the brief left a genuine choice. `.inbox-filter` is four generic declarations wearing a page-specific name. Duplicating the block was out; so the rule became `.inbox-filter, .digest-filter` — one rule, two names, no drift. That is the right call for two pages and I'd argue against it for a third: at that point the honest move is a neutral `.filter-row` and a rename, which was out of scope here because the Inbox is this change's precedent, not its subject.

One test was wrong, and it was mine. I asserted that a profile id too big for Postgres should be a 404, on the reasoning that every other out-of-range id in this app is. It came back 200. `form_id` turns an unusable id into 0, and both filters branch on truthiness, so 0 means "no filter" rather than "not found" — an id that can't name a row has nothing to leak, and the unfiltered page is still only ever the viewer's own. The brief said to guard it exactly as the Inbox does, so the code was right and my assumption wasn't; the test now asserts the real contract and says why, and keeps the assertion that actually matters — that nothing of the other artist's appears. A real id belonging to another artist is still a 404, proven on both `/digest` and `/digest/{day}`, because an empty page there would confirm the id exists.

The access sweeps needed nothing, which I checked rather than assumed. `tests/route_walk.py` has no query-string machinery at all — it fills path placeholders and form fields — and `/inbox` is listed there as a bare path with its own `?profile=` 404 test living beside the feature in `tests/test_web_inbox.py`. Following that precedent puts the digest's four access tests in `tests/test_digest_visibility.py`, where the rest of the digest's access lives. Worth stating plainly, because "the sweep didn't need changing" and "I forgot the sweep" look identical from outside.

One thing deliberately left alone: "The night in numbers" still totals the whole run, filter or no filter. Those counts describe the night's pipeline, not the profile, and scoping them would mean re-cutting `RunStageCount` per profile. It's admin-only and it's honest as it stands, but it's the obvious next question someone will ask of this page.

Eighteen tests, written first and watched failing, each for its own reason — `TypeError` on the keyword that didn't exist yet, a separate `AttributeError` per new field, `200 != 404` on the access ones. Four of the eighteen passed before the implementation and were kept: no-filter-shows-everything, the row staying hidden with one profile, a member's own filter working, an empty `?profile=`. Those four are guards rather than drivers — they exist to catch the version of this change that quietly breaks the unfiltered page, which is the page both artists actually use every morning.

The suite is 1821 passed, 6 skipped, and lint is clean. Nothing pushed, and migration 0007 still hasn't been applied to Neon.
