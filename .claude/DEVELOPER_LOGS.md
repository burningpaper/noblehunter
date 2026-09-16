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

## 2026-09-16 (later): Tables for pitching by email

Migration 0007 lays the ground for pitching curators from inside the digest. Nothing reads or writes these tables yet; this is only the shape. The decision worth recording is who owns a mailbox. It belongs to an **artist**, not to the app and not to a profile, which is what lets a second artist join without sharing Jarred's inbox, and lets two of one artist's profiles pitch from the same address.

Saying that in English is easy; making the database refuse the alternative is the interesting part. `mail_accounts` carries a redundant-looking unique constraint on `(id, artist_id)`, which exists purely so `profiles` can aim a *composite* foreign key at it: `(mail_account_id, artist_id)` must match a real `(id, artist_id)` pair. A profile pointed at another artist's mailbox is then rejected by Postgres itself, not by a check someone can forget to write in a route. `outreach` also records the mailbox a pitch went out from, rather than reading it off the profile, so replies keep threading even after that profile switches address. Profiles gained `open_conversation_limit` and `quiet_after_days` now, while the table was being altered anyway, so the digest can work to them later without another migration.

Two things surfaced that the plan had wrong, and both are worth remembering. The test meant to prove the `direction` check constraint fed it the value "sideways", but that column is `varchar(3)`, so Postgres rejected it for *length* and raised `DataError` rather than the `IntegrityError` the test asked for. The constraint was never exercised. A three-character bad value ("up") fails the way the test intended. The second is still open: the composite foreign key says `ON DELETE SET NULL`, and Postgres applies that to *both* its columns, so deleting a mailbox a profile still points at tries to null `artist_id`, which is NOT NULL, and dies with a confusing `NotNullViolation` instead of tidily letting go. Nothing deletes mailboxes yet, so it costs nothing today. When something does, the fix is `ON DELETE SET NULL (mail_account_id)`, which Postgres has supported since 15 — and disconnecting a mailbox should probably stamp `disconnected_at` rather than delete the row at all, which is why the column is there.

## 2026-09-16 (later still): A small Gmail client

`core/gmail.py` is the second piece of the pitching feature and still nothing calls it. It holds a refresh token the caller has already decrypted, swaps it for a short-lived access token, caches that in memory, and exposes exactly five calls: `profile`, `send`, `history_since`, `message`, `thread`. It lives in `core/` rather than `pipeline/` because both halves of the system need it — the Vercel web app sends, the Mac Mini runner reads replies — and so it depends on nothing heavier than httpx. It is modelled deliberately on `pipeline/web.py`, down to the explicit per-request timeout and the error type carrying a `kind`.

That `kind` is the whole point of the module. An HTTP status code is the wrong thing to hand a route, because three of Google's refusals mean three unrelated things to a person. `auth` (a refused refresh token, a 401, a 403) means a human must reconnect the mailbox and no amount of retrying will help. `transient` (429, any 5xx, a dead socket) means try again later. `rejected` means Gmail understood perfectly and said no, so retrying sends the same bad message again. And `history-gone` — a 404 from the history endpoint specifically, because Gmail forgets history older than about a week — means the caller should stop asking what changed and re-read whole threads instead. Sorting those once, here, keeps four different recovery strategies out of every future caller.

The security property is enforced by two tests rather than a comment: no refresh token, access token, address or message body may appear in an error message. Network failures therefore name only the exception's *type*, never httpx's own text, which happily includes the URL; and a failed API call names only the path segment it was calling ("messages", "history") with every id stripped. Keep that true of any logging added later. Two soft spots are worth knowing before Task 4 builds on it: `send` reads `id` and `threadId` straight out of the response, so a shape Google has never actually returned would escape as a bare `KeyError` rather than a `GmailError`, and `response.json()` on a non-JSON 200 (a captive portal, an HTML error page) would raise `ValueError` the same way — `pipeline/web.py` guards that case in `WebSearch` and this module does not. Neither is reachable from the tests; both would be cheap to close the first time a caller cares.

## 2026-09-16 (evening): The message itself

`core/mime.py` is the third piece of the pitching feature and, like the two before it, nothing calls it yet. It does two small things in opposite directions: `build_message` turns a from, a to, a subject and a body into the base64url MIME blob `Gmail.send` wants, and `parse_message` turns the nested payload Gmail gives back into the handful of fields Noble Hunter stores.

It is plain text and nothing else. No attachments, no HTML alternative, no tracking pixel. That is partly taste, since it's what a curator's inbox wants from a stranger, and partly what keeps the module small enough to hold in your head.

The reason it's a file of its own rather than four lines inside the send route is header injection. A newline inside an address or a subject ends that header and starts a new one, so an address typed into a form could carry `\nBcc: somebody@else.com` and quietly copy a third party on every pitch. `_header` refuses any value holding `\r` or `\n` before `EmailMessage` ever sees it, so the refusal is ours and predictable rather than whatever the standard library happens to do this release. The body is deliberately exempt: a line reading `Bcc: this is just text` is just text, and a test insists it survives.

The other half is quoted history. A reply usually carries the whole thread underneath it, so `parse_message` splits at the "On <date> … wrote:" line, and at the couple of other separators clients use, and keeps what a person actually wrote apart from what they merely quoted. Otherwise a conversation would show the same paragraph five times down the page.

The test that earns its keep is the round trip. Gmail hands our own sent messages back through the reply sync, so anything we build has to survive being parsed by the same module. Build it, decode it, feed its body back through `parse_message`, and the original body must come out. `set_content` adds a trailing newline and `get_content()` returns it, which is why `_as_text` strips.

Two things in the plan were wrong, and both were in the test rather than the code. The `sent_at` assertion paired an `internalDate` of `1_789_000_000_000` with 2026-09-06 22:26:40, but that epoch is really 2026-09-10 00:26:40; the asserted time would need 1788733600, which is nobody's round number. The round epoch was plainly the deliberate half, so the date literal was corrected rather than the assertion loosened. And the round-trip test called `message_from_bytes(...)` then `.get_content()`, which the legacy `email.message.Message` doesn't have — `get_content` arrived with `EmailMessage` — so the parse needs `policy=policy.default`. No change to the module could have fixed that one.

## 2026-09-16 (late evening): Whose mailbox is it

`core/mailboxes.py` is the fourth piece of the pitching feature and the first one a person will feel. It connects a Gmail mailbox to an artist, lends it to that artist's profiles, and takes it back. Task 6's routes and the Google consent screen sit on top of it; nothing in here talks to Google at all. `connect_mailbox` is handed a refresh token the route has already obtained, which is exactly what lets the whole module be tested without a network.

The rule it exists to state is the one migration 0007 already enforces: a profile may only pitch from its own artist's mailbox. The database refuses the alternative with a composite foreign key, and this module refuses it earlier, in a sentence. Both were kept deliberately. The constraint is the half that will still be true after someone writes a route that forgets to ask; the message is the half a person can act on, because Postgres's version of that complaint names a constraint rather than a mistake.

Connecting the same address twice refreshes the existing row instead of adding a second one. Google issues a brand-new refresh token every time someone walks through consent, and the old one may already have been revoked from their account page, so a second row would be a second, silently dead copy. Reconnecting is also the *repair* path: it clears `needs_reconnect`, `last_error` and `disconnected_at` together, so the mailbox that has been failing all week is fixed by precisely the action a person would guess.

Letting go is the subtle one. `detach_mailbox` returns the mailbox only when no profile still points at it, and that return value is a signal to the caller — "nobody uses this, you may revoke it at Google" — not something this module acts on. Nothing revokes anything yet. The ordering inside it is load-bearing: the profile is detached and flushed *before* the "is anyone still using this?" query runs, so the profile letting go doesn't count itself and a single-profile mailbox would never be handed back at all. Only then is the stored token cleared and `disconnected_at` stamped. The row itself stays, because `outreach` and `email_messages` still point at it under `ON DELETE RESTRICT`, and because past conversations should keep reading correctly forever.

Two refusals in here look alike and aren't. Another artist's mailbox is a permissions problem; a disconnected one is a state problem. Both raise `MailboxProblem`, and the tests match on distinct phrases so the two can't quietly collapse into one vague message.

One loose end for Task 6. `connect_mailbox` finds an existing row case-insensitively with `func.lower`, but the unique constraint is on the raw address, and there is no functional index behind it, so nothing at the database level stops `Synman@gmail.com` and `synman@gmail.com` both existing for one artist. If they ever did, the lookup's `scalar()` would pick one arbitrarily rather than complain. Reconnecting also keeps whichever spelling was stored first, never the new one. Neither is reachable through this module alone, which is the only thing writing that column today, so nothing was changed. But the rest of the codebase lowercases an address before storing it (`core/people.py`, `core/access.py`, `core/contacts.py`), and that is the cheap fix the moment a route writes an address by some other path.

One more for Task 9, which has to send. Two unrelated exception families sit behind "I couldn't get a token". `refresh_token_for` raises `MailboxProblem`, a `ValueError`, when the mailbox isn't connected any more; `core/mail_crypto` raises `MailNotConfigured`, a `RuntimeError`, when the key is missing or can't read what's stored. The sender must catch both, and they deserve different words: reconnect this mailbox, versus this machine isn't configured to send at all.
