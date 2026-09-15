# Artists and access: design

**Date:** 2026-09-15
**Status:** Approved in conversation; awaiting review of this write-up.
**Comes before:** [Inline email pitching](2026-09-15-inline-email-pitching-design.md)

## Why

Noble Hunter was built for one musician. Everyone on `ALLOWED_EMAILS` sees every profile, every digest and every brief. The rules that stop a curator appearing twice also assume a single artist:

- A curator reaches at most one digest every 90 days, across all profiles.
- "Bad fit" hides a curator from everyone, forever.

Jarred wants to invite other artists to use it for their own promotion. Once email pitching lands, a shared app would also mean a shared inbox: any invitee could read Synman's conversations and send from his Gmail.

So before any email work, the app learns who owns what. Each artist sees only their own work. Artists stop competing for the same curators.

## Decisions

| Question | Decision |
|---|---|
| Visibility | People see only their own artists' profiles, digests and mail. Admins see everything |
| Curators across artists | Separate per artist: own 90-day window, own "bad fit" list. "Dead" stays shared |
| Inviting | In the app, by admins. No more redeploying to change `ALLOWED_EMAILS` |
| Admins | Everyone listed in `ALLOWED_EMAILS` |
| Shared costs | Nightly Claude budget and search credits stay shared, set by admins |

## Concepts

- **Artist.** A named group that owns profiles, and later mailboxes. Jarred's existing profiles move under an artist called "Synman".
- **Member.** A person (a Google account email) who works on an artist. One person can belong to several artists, for example a manager.
- **Admin.** Anyone whose email is in `ALLOWED_EMAILS`. An admin sees and manages everything, and doesn't need to be a member.

## What people see

**Signing in.**
- **Who gets in:** a verified Google email that is either an admin or a member of at least one artist. Everyone else gets today's access-denied page.
- **Access is checked on every request, not just at sign-in.** Removing someone takes effect on their next click, not when their 14-day session expires.

**People page (admins only).**
- **Nav link:** "People".
- **Lists:** every artist with its members.
- **Actions:**
  - add an email to an existing artist, or to a new artist created on the spot;
  - remove a member from an artist;
  - rename an artist.
- **Invites:** there's no invitation email. Jarred tells the person, and they sign in with that Google account.
- **Deleting artists:** not offered, since their history would need somewhere to go.

**Profiles page.**
- **Members** see their artists' profiles, grouped by artist when they have more than one.
- **Admins** see all profiles, grouped by artist.
- **Creating a profile:** a new profile belongs to one of your artists, picked from a list when you have more than one.
- **Names:** profile names must be unique within an artist, not across the whole app.
- **Moving profiles:** a profile never moves between artists.
- **Admin-only cards:** the Claude budget card and **Run now** are admin-only.
- **Runner panel:** the status panel stays visible to everyone. It shows run times and warnings, nothing belonging to an artist.

**Digest page.**
- Members see entries for their artists' profiles.
- Admins see all entries, grouped by artist then profile.
- The night's pipeline counts at the bottom stay whole-app numbers.

**Everything else** (profile pages, Ask Claude, verdicts, and later the mail routes) follows one rule. Asking for something you can't see returns **404 Not Found**, so an outsider can't tell whether it exists.

## Curators, separate per artist

- **The 90-day window becomes per artist.**
  - `outreach` gains `artist_id`, copied from the profile when the entry is created. Profiles never move between artists, so it can't drift.
  - The EXCLUDE constraint changes from `(curator_id, 90-day range)` to `(artist_id, curator_id, 90-day range)`.
  - Two different artists can each have the same curator in a digest. One artist's profiles still share a single window.
- **"Bad fit" becomes per artist.** A new table `artist_curator_exclusions` holds `artist_id`, `curator_id`, `reason` and `excluded_at`, with the pair as primary key.
  - Marking an entry bad-fit adds a row for that entry's artist.
  - The migration moves every existing bad-fit mark on `curators` to Synman, then clears it.
- **"Dead" stays on the curator** (`curators.excluded_at`, reason `dead`), because an abandoned playlist is abandoned for everyone. The curators check constraint narrows to `dead`.
- **Exclusion functions take an artist.**
  - `curator_is_eligible(session, curator_id, artist_id, today)` is false when any of these is true:
    - the curator is dead;
    - the artist has a bad-fit mark for them;
    - the artist has an entry for them in the last 90 days.
  - `contact_is_excluded(...)` also takes `artist_id`, applying the same rule to the contact's curator.
- **Digest.**
  - Candidates are qualified fits for active profiles on playlists that are either `qualified`, or `digested` within the last 90 days. A playlist digested for one artist still counts for another.
  - Eligibility and "already used tonight" are tracked per `(artist, curator)`.
  - A playlist is still set to `digested` when its first entry is made. This keeps discovery's 90-day re-fetch rule unchanged.
- **Research** still runs once per playlist, because contacts are public facts shared by everyone. It skips a playlist only when its curator isn't eligible for any artist whose profile qualified it. Its contact check asks whether the contact is blocked for every such artist.
- **`pipeline report`** checks eligibility against the artist of each fit it lists.

## Shared costs

- **Shared by everyone:** the nightly Claude budget, the search API credits and the Mac Mini's run. Research spends the budget in digest ranking order across all artists.
- **Per profile:** the digest target (at most 50) stops any one profile taking the whole night.
- **Deferred:** splitting the budget by artist until it's actually a problem.

## Known limit

A new artist's profiles are matched only against playlists found or re-checked after the profile is created. Qualifying the existing pool against a new profile is a sensible follow-up, but it isn't part of this project.

## How it works

### Data (migration 0006)

- **`users`** columns:
  - `id`
  - `email` (unique, lowercase)
  - `name`
  - `picture_url`
  - `created_at`
  - `last_signed_in_at`
- **`artists`** columns:
  - `id`
  - `name` (unique)
  - `created_at`
- **`artist_members`** columns:
  - `artist_id`
  - `user_id`
  - `added_at`
  - `added_by`

  The primary key is `(artist_id, user_id)`.
- **`artist_curator_exclusions`**, as above.
- **`profiles`** changes:
  - gains `artist_id` (not null);
  - the unique constraint on `name` becomes unique on `(artist_id, name)`.
- **`outreach`** gains `artist_id` (not null). Its EXCLUDE constraint is replaced as above.
- **Data migration:**
  1. If any profiles exist, create artist "Synman".
  2. Point every profile and outreach row at it.
  3. Move bad-fit marks.

  On an empty database (tests, a fresh install) nothing is created. The migration never creates users: it can't see `ALLOWED_EMAILS`, which lives in Vercel. A `users` row is created or updated on every successful sign-in. Admins need no membership, since they see everything, and Jarred can add himself to Synman on the People page if he wants.
- **Roles:**
  - `noble_web` gets write on `users`, `artists`, `artist_members` and `artist_curator_exclusions`.
  - `noble_pipeline` gets read on the new tables. Its existing grants cover `outreach` and `curators`.
  - Run `db grant` after migrating, and migrate Neon before pushing.

### Units

| Module | One job |
|---|---|
| `core/people.py` | Users, artists and memberships: add a member (creating the user if needed), remove, rename, list |
| `core/access.py` | The single source of "who can see what": `Viewer(email, is_admin, artist_ids)`, `viewer_for(session, email, admin_emails)` (None means no access), and `visible_profiles`, `can_see_profile`, `can_see_outreach`, `require_*` (raising `NotVisible`) |
| `core/exclusion.py` | Existing module, now artist-aware as described above |
| `web/access.py` | FastAPI dependency: builds the `Viewer` per request, maps `NotVisible` to 404, and provides `require_admin` |
| `web/people.py` | The People page and its actions |

**Sign-in.**
- **Callback:** the sign-in callback asks `viewer_for` instead of checking `allowed_email_set`. The session stores the email only. The guard middleware still handles sign-in and CSRF.
- **Per request:** `web/access.py` rebuilds the viewer on every request. A signed-in email that no longer has access gets its session cleared and is sent to the access-denied page.

**Every existing route** takes the viewer and passes it to `core`:

- `profiles`, `profile_contents` and `suggestions` check the profile.
- `digest` filters by visible profiles.
- The verdict route checks the outreach entry.
- `runs/request` and `settings/claude-budget` require an admin.

Read functions in `core/digest_view.py` and `core/profiles.py` gain a viewer argument, so filtering happens in the query rather than after it.

## Error handling

- **No access.** Something belonging to another artist returns 404 with the normal not-found page; htmx requests get a 404 fragment. An admin-only action attempted by a member returns 403.
- **Removed mid-session.** The next request clears the session and shows access-denied.
- **Adding a member.** An invalid email, or adding someone already on the artist, shows an inline error on the People page. Admins can't be removed on the People page: admin status comes only from `ALLOWED_EMAILS`.
- **Nothing to show.** A member with no artists can't sign in. An artist with no members still shows on the People page.

## Testing

TDD against the Docker Postgres.

- **Access, the safety net.**
  - A parametrized test walks every registered route as a signed-in member of a different artist, with real ids from another artist's data. It expects 404 for anything belonging to an artist and 403 for admin-only actions.
  - A test fails if a route is added without an entry in that walk, so new routes can't skip access.
- **Viewer.** Admin by env, member via database, no access, removal takes effect on the next request.
- **People.** Add to existing or new artist, duplicate, invalid email, remove, rename, non-admin refused.
- **Exclusion.**
  - Same curator in two artists' digests on the same night.
  - One artist can't get the same curator twice within 90 days: both the function and the database constraint refuse.
  - Bad-fit blocks only that artist; dead blocks everyone.
  - Contact exclusion is per artist.
- **Digest.** A playlist digested for artist A reaches artist B, and per-artist used-curator tracking works.
- **Research.** It skips only when no artist that qualified the playlist is eligible.
- **Migration.** Run on a copy of today's shape: profiles and outreach land on Synman, bad-fit marks move, and the new constraint holds.
- **Existing tests** gain an artist in their factories. Their behaviour is unchanged for a single artist.

## Build order

1. **People and access.** Tables, migration, viewer, sign-in by membership, People page, admin-only cards.
2. **Scope every route.** Profiles, digest, suggestions and verdicts filtered by viewer, plus the route-walk test.
3. **Curators per artist.** Outreach `artist_id`, new constraint, bad-fit per artist, artist-aware exclusion, digest and research.

Each stage is pushed only after Neon is migrated. Nothing changes for Jarred when stage 1 ships: he is an admin and a member of Synman.

## Jarred's setup

- **Invitees:** set the Google project's publishing status to **In production**, so invitees don't have to be added as Google test users. Sign-in asks only for email and profile, so there's no warning screen.
- **Adding people:** the People page.

## Out of scope

- Invitation emails.
- Roles other than admin and member.
- Deleting artists.
- Per-artist budgets or search quotas.
- Qualifying existing playlists against a new profile.
- Moving a profile to another artist.
