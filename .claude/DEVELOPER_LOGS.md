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
