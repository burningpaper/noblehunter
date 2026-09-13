# Curator Discovery Pipeline — Spec

**Scope:** A daily, unattended job that produces 10–20 qualified Spotify playlist curators, each with a verified contact route and a short brief, ready for Jarred to pitch by hand. Nothing in this spec sends messages. Tracking of replies and placements is out of scope for v1 and is only touched where v1 needs to leave hooks for it.

**Success condition:** On a typical morning the digest contains at least 10 contacts Jarred would actually write to, fewer than 2 he'd reject on sight, and zero he has already contacted.

---

## 1. Why this scope

The expensive part of playlist outreach is not writing messages; it's the research chain between "there's a playlist" and "here is a real person, this is how to reach them, and this is why they'd care." That chain is 10–15 minutes of tab-hopping per playlist with roughly a one-in-three yield. Automating it turns a full day of grind into an hour of writing. Everything else — sending, follow-ups, DMs — is either cheap to do by hand or risky to automate, so it stays manual.

## 2. Inputs

**Artist profile (static, edited rarely).** A short YAML or JSON file describing Synman: genre tags in priority order (IDM, braindance, ambient electronica, experimental electronic, etc.), 5–10 reference artists whose presence on a playlist signals fit, the current track(s) being pushed with Spotify links and a one-line description each, and a list of "anti-signals" — artists or terms whose presence means wrong playlist (e.g. lo-fi study beats, EDM, big-room).

**Seed queries (static, tweaked occasionally).** A list of search phrases the discovery step cycles through. Start with ~30: genre terms, subgenre terms, mood terms that map to the music ("late-night electronics", "glitchy ambient"), and reference-artist names combined with "playlist".

**Exclusion list (dynamic, machine-maintained).** Every playlist ID and every contact (email, handle, domain) that has already appeared in a digest, plus everything Jarred marks as rejected. This is the one piece of state that must be perfect; a duplicate in the digest is the most visible failure.

**Jarred's feedback (dynamic, daily, optional).** A one-word verdict per contact from the previous digest: `pitched`, `skip`, `bad-fit`, `dead`. Used for exclusion and, later, for tuning.

## 3. Pipeline

The job runs nightly (say 02:00 SAST) so the digest is waiting in the morning. Five stages, each writing to its own table so a failure in one doesn't lose the work of the previous.

### 3.1 Discover

Pull candidate playlists from two sources and union them. First, web search restricted to `open.spotify.com/playlist` for each seed query, taking the top 20–30 results. Second, "neighbour" discovery: for every playlist that reached the digest and was marked `pitched`, search for other playlists by the same curator and playlists that appear alongside it in search results for its own name. Neighbour discovery is where the good stuff comes from after week one, because it follows the social graph of curators who actually work in this corner of music.

Target: 150–300 raw candidates per night. Dedupe against the exclusion list before any of them cost a page fetch.

### 3.2 Fetch and parse

For each surviving candidate, fetch the public playlist page and extract: name, description (raw text and any URLs, emails, or @handles inside it), owner display name and owner profile URL, follower count, track count, the full track list with artist names and the date each was added, and the playlist cover image URL (some curators embed contact details in the artwork; skip this in v1 but keep the URL).

Practical note: the public web page renders enough of this server-side or via a well-known JSON blob that a headless browser or a plain fetch plus parsing works today. The official API does not return track lists for playlists you don't own as of February 2026, so this stage deliberately uses the web page, not the API. Rate-limit to something polite — one fetch every few seconds — and expect to rework the parser once or twice a year when Spotify changes markup.

### 3.3 Qualify

Score each playlist on four dimensions, then reject anything below threshold. This is the stage that makes the difference between a numbers game and a targeted list.

**Alive.** Most recent track add within 60 days; at least 3 adds in the last 6 months. Playlists that haven't moved in a year are dead regardless of follower count.

**Fit.** Compute overlap between the playlist's artists and the reference-artist list, and a soft genre score from an LLM classification of the track list ("what genre is this playlist, in three tags"). Any anti-signal artist present is a hard reject. Playlists with 3+ reference artists are top-tier fit; 1–2 is acceptable; 0 needs a strong LLM genre match to survive.

**Real.** Detect the pay-to-play and bot patterns: follower count wildly out of proportion to track adds; hundreds of tracks from artists who have almost no other presence; description text mentioning submission fees, "guaranteed placement", or a SubmitHub/Groover-only route; track lists that are 100% tiny unknown artists with no anchoring known names. These are rejected, not just down-ranked, because pitching them wastes Jarred's time and some of them attract Spotify's artificial-streaming enforcement.

**Reachable.** Not a rejection criterion here, but noted: playlists owned by Spotify itself, by labels, or by distributors with formal submission processes are tagged as such so the brief can say "submit via their form" rather than "email this person".

Size band is recorded (under 500, 500–2k, 2k–10k, 10k+) but not scored. Small active playlists by real curators often convert to saves and follows better than large ones, and the point of v1 is reachable humans, not the biggest numbers.

### 3.4 Find the human

For each qualified playlist, run a contact-resolution agent with a strict budget (say 10 page fetches and 60 seconds). It works through a ladder and stops at the first confident hit:

1. Email or handle in the playlist description itself.
2. Link in the description → follow it (Linktree, personal site, label page) → find email, submission form, or social handle.
3. Owner display name → search Instagram, then X/Bluesky, then a general web search for `"<display name>" playlist curator`. Accept a match only if the profile bio references playlists, curation, or the playlist name, or if it links back to the Spotify profile.
4. Owner has other playlists → repeat steps 1–2 on those (curators often put contact info in only one of their playlists).
5. Give up. Record the playlist as `no-contact` and keep it in the pool; retry in 90 days.

Every contact gets a confidence grade. **A** — explicit, curator-stated route (email or form in their own description or link page). **B** — social profile matched by name with at least one corroborating signal. **C** — name-only match with no corroboration. Only A and B reach the digest. C is stored for a manual look if the playlist is exceptionally good.

Record the route type too: `email`, `submission-form`, `instagram`, `x`, `other`. Email and form are preferred; Instagram and X are noted as "manual DM" routes.

### 3.5 Brief and rank

For each contact-resolved playlist, an LLM writes a brief of roughly 80–120 words with a fixed shape: who the curator appears to be (name, what else they do, one sentence), what the playlist is (genre in their own terms, size band, how active), why Synman fits (name the 2–3 reference artists on it and the specific track of Jarred's that sits closest), and how to reach them (route, contact, and any submission instructions they've stated — "no attachments", "one track only", "include a short bio"). The brief must quote or closely paraphrase the curator's own description where it says anything about what they want; that line is what makes a pitch land.

Rank by a simple weighted sum: fit first, contact confidence second, alive third, size band last. Take the top 20. If fewer than 10 qualify, the digest says so plainly rather than padding with weak entries — a short honest list beats a long one that erodes trust in the system.

## 4. Output

**Daily digest.** One email (or a Markdown file dropped somewhere Jarred reads) with the ranked list. Each entry: playlist name linked, curator name, size band, activity, route and contact, the brief, and a one-line "suggested angle". At the bottom: counts for the night (candidates found, qualified, contacts resolved, and how many were dropped at each stage) so drift in the pipeline is visible without digging.

**Tracker rows.** Every digest entry is also written as a row in a tracker with status `new`. Jarred's only obligation is to flip that status when he acts. This is the hook for v2 (reply and placement tracking) and it costs nothing now.

## 5. Data model

Four tables. Names are indicative.

`playlists` — spotify_id, name, description, owner_name, owner_url, followers, track_count, last_add_date, size_band, fit_score, alive_score, real_flag, genre_tags, reference_artists_present, first_seen, last_checked, status (`candidate`, `qualified`, `rejected`, `no-contact`, `digested`).

`contacts` — id, playlist_id, route_type, value, confidence (A/B/C), source_url, resolved_at, notes.

`curators` — id, display_name, contact_ids, playlist_ids. One curator often owns several playlists; pitching them once for the best-fit playlist is better than pitching three times.

`outreach` — id, curator_id, playlist_id, digest_date, brief_text, status (`new`, `pitched`, `skip`, `bad-fit`, `dead`, `replied`, `placed`), pitched_at, notes. v1 only writes `new` and reads Jarred's status updates.

The exclusion check for stage 3.1 is: playlist ID in `playlists` with any status other than `no-contact` older than 90 days, or curator ID in `curators` with any outreach row.

## 6. Feedback loop (lightweight, v1)

Jarred's daily verdicts do two things immediately: `pitched` promotes the curator's other playlists into neighbour discovery; `bad-fit` and `dead` add the curator to exclusion. Weekly, a short report shows which seed queries produced pitched contacts versus rejected ones so he can prune the query list by hand. No automatic retuning in v1 — the volume is too small for it to mean anything for months.

## 7. Implementation notes

This fits naturally on the existing Mac Mini setup: a LangGraph graph with one node per stage, scheduled by launchd, Postgres on Neon for the tables, Playwright for the fetch stage, and Claude for the qualify, contact-resolution, and brief steps. The contact-resolution agent is the only part that needs real agentic tool use (browse, search, follow links); the rest is deterministic with an LLM call for classification or writing. Budget the agent hard — one runaway contact search can eat the night's token spend.

A realistic first-week expectation: 200 candidates a night, 40–60 pass qualification, 15–25 get an A or B contact, 10–20 in the digest. Contact resolution is where the yield is lost and where a week of looking at failures will most improve the system.

## 8. Explicitly out of scope for v1

Sending any message on any channel. Instagram or Facebook automation of any kind. Placement detection (checking playlists for Synman tracks) and stream attribution from Spotify for Artists — both are v2, and the tracker rows above are the only preparation v1 makes for them. Playlist-cover OCR. Any use of the official Spotify API.

## 9. Open questions

Whether the digest should be an email, a Markdown file in a synced folder, or rows in the existing n8n/Neon social-ops app. Whether to fold in Bandcamp, blog, and radio contacts (A Closer Listen, Fluid Radio and their peers) as a second "publication" lane using the same contact-resolution agent — the machinery is identical and those placements arguably matter more for this music than Spotify playlists do. And whether 10–20 a day is the right number once the writing habit settles, or whether 5 very good ones with longer briefs would produce more placements per hour spent.