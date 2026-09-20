# Discovery from the artists you sound like: design

**Date:** 2026-09-20
**Status:** Measured in a spike; awaiting review of this write-up.
**Depends on:** nothing new. It adds a second discovery source beside the search providers, and everything downstream — evaluate, qualify, research, digest — is untouched.

## Why

Discovery asks Google and Brave for `site:open.spotify.com/playlist <term>`. That worked, and then it stopped: by 2026-09-17 nearly every search term was returning about fifty results with **zero new playlists**. Thirteen terms, all mined out. The night of the 17th produced one digest entry.

Adding terms doesn't fix it, because the limit isn't the terms. A playlist reaches a search index by being *linked to from the web*, and the best-linked Spotify playlists are blog-era ones nobody has touched since 2019. That is the 66% dead rate: not bad luck, but the shape of the index.

Jarred's instinct was that thousands of relevant playlists must exist. They do. They are simply not in Google.

**A spike on 2026-09-20 measured the alternative.** A logged-out Spotify artist page fires a GraphQL operation, `queryArtistOverview`, whose response contains a section named `discoveredOnV2` — the playlists Spotify's own data says people discovered that artist through. Across the IDM profile's 18 reference artists:

| | |
|---|---|
| Pitchable playlists (after dropping Spotify's editorial ids) | 96 unique |
| Never seen by five days of web search | **90 (94%)** |
| Fetched and judged by the pipeline's own rules | 99 |
| **Qualified** | **29 (29%)** |
| not-alive | 60 (60%) |
| no-fit | 8 |
| too-small | 2 |

For comparison, web search has produced **9 qualified playlists from 1,239** over five days. One pass over the reference artists, taking about ten minutes, produced three times as many qualified leads.

**Two results that matter more than the headline.**

The 94% non-overlap is the real finding. This is not a better way to search the same pool; it is a different pool. Each reference artist opens a different corner of it — Clark and Luke Abbott yielded 12 and 11, while Bonobo and Bibio yielded 3 and 2, because the big crossover artists sit in editorial playlists that get filtered out and the mid-sized ones surface actual curators.

The dead rate is **not** improved: 60% against search's 66%, median last add 196 days. The expectation going in was that "Discovered on" would be structurally fresher because Spotify computes it from recent listening. That was wrong, and the wrongness is instructive — the listening is often people playing curated retrospectives (`Aphex Twin - sleep mix`, `Boards of Canada: Complete`, `Best of Ólafur Arnalds`) that nobody updates. It is a listening signal, not a freshness signal.

So the gain is volume and reach, not precision. That is still a large gain, because the existing filters already handle precision and the thing they were starved of was supply.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Replace search, or sit beside it? | **Beside it.** Both run; `playlist_sources.provider` already records which found what | 94% non-overlap means they are complementary. Search is also the only thing that works for a profile with no reference artists yet |
| Where artist ids come from | Resolve each reference artist once, **store it** on `reference_artists.spotify_artist_id` | The column exists (migration 0001) and has never been filled — 0 of 27 rows. Resolution is a second fetch path and the least reliable part of this; doing it once per artist rather than nightly keeps that fragility off the critical path |
| Artist-owned playlists | **Drop them** when the owner is one of the profile's own reference artists | The spike qualified playlists owned by Bonobo and Four Tet. Pitching Bonobo to put Synman on Bonobo's playlist is not a lead |
| Which section | `discoveredOnV2` only | `featuringV2` was 8-for-8 Spotify editorial in the spike — all unpitchable. Worth naming so nobody adds it later thinking it was overlooked |
| How often | Once per profile per nightly run, before the search step | An artist's "Discovered on" changes slowly. Nightly is already more often than it moves |
| Fit checking | **Unchanged for now**, but see Out of scope | The fit check is visibly leakier on this source (`GTA V BANGERS`, `Lullabies for Sleep`, `best piano songs` all qualified). Tightening it is a separate change with its own risk of over-rejecting |

## What people see

Nothing new on screen. This changes where leads come from, not how they are worked.

The run report gains a line in the discover stage, beside the per-term lines:

```
IDM Playlists
  Discovered on: 18 artists, 96 playlists, 90 new
  Ambient Techno: 46 found, 1 new, 0 requeued, 45 skipped
  ...
```

A profile whose reference artists have never been resolved says so once, rather than silently contributing nothing:

```
  Discovered on: 4 of 18 artists have no Spotify id yet; they were looked up and stored
```

## How it works

### Units

- **`pipeline/artist_ids.py`** — resolve a reference artist's name to a Spotify artist id, logged out, and store it. Its own module because it is the fragile part: a name that matches nothing, or matches the wrong act, is a known failure with a known answer (record nothing, report it, carry on).
- **`pipeline/discovered_on.py`** — given an artist id, return the playlist ids in `discoveredOnV2`, dropping Spotify's editorial prefix. Pure parsing over a captured payload, so it is testable from a fixture without a browser.
- **`pipeline/spotify.py`** — gains `fetch_artist_overview(artist_id)` beside the existing `fetch_playlist` and `fetch_user_playlists`, reusing the same rate limiter, retries and logged-out browser.
- **`pipeline/discover.py`** — gains the new source alongside the search providers, attributing what it finds and applying exclusion before any fetch, exactly as it does today.

### Data

**There is a migration, 0008.** `reference_artists.spotify_artist_id` already exists and is nullable, so filling it needs nothing. But `playlist_sources.provider` is guarded by `one_of("provider", SourceProvider)`, and `one_of` builds a real `CheckConstraint` — `provider in ('serper', 'brave', 'neighbour')`. Adding a fourth value means altering that constraint on the production database.

Worth noting what is already in that list: **`neighbour`**, defined and unused, reserved for Stage 9's curator traversal. So the enum has always anticipated non-search discovery; this is the first source to actually use the idea.

Migration 0008 therefore drops and recreates one check constraint and touches no data. It is still a migration against Neon, with the usual rule: migrate before pushing, because Vercel deploys on push and a deployed app against an unmigrated database breaks every page.

### Fetching

The technique is the one Stage 0 proved and `pipeline/spotify.py` already uses: headless Chromium, no login, let the web player make its own GraphQL call, read the response. The spike confirmed both the artist page and the search page work this way logged out.

**Pacing matters.** The existing client rate-limits to one fetch every three seconds. Artist pages are additional page loads — 18 per profile per night. That is small, but it is 18 more than today, and Stage 0's warning about reCAPTCHA Enterprise stands: it loads on these pages and has never yet challenged us. Artist resolution happens once per artist ever, not nightly, which keeps the recurring cost to one page load per reference artist.

**Failure is per-artist and never fatal.** An artist page that times out, gets blocked, or returns no `discoveredOnV2` section costs that artist's contribution and nothing else. The run continues; the report says which artists were skipped. This mirrors how a failing search provider is already handled.

### Ordering

Discovered-on runs *before* the search step, so that a night which finds plenty from the artist graph still runs search and records what it finds. Both feed the same candidate pool and the same exclusion checks. Nothing downstream needs to know which source a playlist came from, except the report and `playlist_sources`.

## Testing

- **Parsing** from a captured `queryArtistOverview` fixture: the ids in `discoveredOnV2`, editorial ids dropped, `featuringV2` ignored, and a payload with no such section yielding nothing rather than raising.
- **Artist resolution** against a captured search payload: a clean match, a name that matches nothing, and a name whose top hit is a different act — the last one recorded as a miss rather than a wrong id, because a wrong id silently pollutes discovery for as long as it is stored.
- **Owner filtering**: a playlist owned by one of the profile's own reference artists is dropped.
- **Discover integration**, against the Docker Postgres: new ids become candidates with the right provider attribution; ids already known are skipped; exclusion is applied before any fetch.
- **Failure paths**: one artist failing does not stop the others; a profile with no reference artists contributes nothing and says so.
- **No live calls in the suite.** One opt-in live test, marked as the existing ones are, that fetches a single real artist page — so drift in Spotify's payload shape is discoverable on purpose rather than at 02:00.

## Build order

1. `fetch_artist_overview` on the Spotify client, with a captured fixture.
2. `discovered_on.py` parsing, from that fixture.
3. `artist_ids.py` resolution and storage, with its own fixture.
4. Wire into `discover.py`, with attribution and the report lines.
5. Owner filtering.
6. A live run against the IDM profile, compared against the spike's 29-of-99.

Each step is independently useful; the first three are pure functions over fixtures and need no browser.

## Out of scope

- **Replacing web search.** Both run.
- **Tightening the fit check.** The spike showed it passing `GTA V BANGERS` and `Lullabies for Sleep` on genre words in a description. That is a real cost — five cents of research each — but changing fit affects every source and every profile, and it deserves its own measurement rather than being smuggled in here.
- **Neighbour discovery** (a curator's other playlists, Stage 9). Adjacent and appealing, and the reason to keep this change small is so that it can follow.
- **The follower floor.** Two playlists were rejected as too-small. Small curators reply more often than large ones, and the floor may be wrong for this source — but that is a settings question, not a discovery one.
- **Spotify's own playlist search**, the other idea from the same conversation. Larger, and worth measuring separately.

## Risks

**This is unofficial.** Spotify does not document `queryArtistOverview`, and the payload shape can change without notice. The mitigation is the same as for the existing fetcher: parse defensively, fail per-artist, and keep one opt-in live test that will notice drift before a nightly run does.

**reCAPTCHA.** Never triggered so far, at spike and nightly volumes. This adds 18 page loads a night for one profile. If it ever fires, discovery degrades to search rather than the run failing — worth building that way from the start.

**The dead rate is unchanged.** 60% of what this finds is not worth pitching. The value is that the remaining 40% is four times larger than what search now yields, and almost entirely unseen.
