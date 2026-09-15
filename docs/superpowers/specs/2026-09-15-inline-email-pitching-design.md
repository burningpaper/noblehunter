# Inline email pitching: design

**Date:** 2026-09-15
**Status:** Approved in conversation; awaiting review of this write-up.
**Depends on:** [Artists and access](2026-09-15-artists-and-access-design.md). Mailboxes belong to artists, and only an artist's members can use them.

## Why

The digest tells an artist who to pitch and why. The pitching itself happens elsewhere: copy an address, open Gmail, write from scratch, send, then come back to click "Pitched". Replies land in an inbox full of everything else and never make it back to Noble Hunter.

This feature closes that loop for email contacts:

1. The artist writes the pitch inside the digest entry, with a Claude draft to start from.
2. It goes out from the Gmail that entry's profile pitches from.
3. The curator's reply appears under the same playlist, and the artist can answer from there.

It changes one line of `spec.md`, which said "nothing in this spec sends messages". That still holds for everything automatic. Noble Hunter never sends on its own: every message goes out because someone pressed Send.

Instagram stays manual. Meta's API only lets a business reply to someone who wrote first, and the unofficial tools log in with the account's password and risk it being disabled.

## Decisions

| Question | Decision |
|---|---|
| Mailbox | Belongs to an artist; each profile chooses which of its artist's mailboxes it pitches from |
| Connection | Gmail API with OAuth scopes `gmail.send` and `gmail.readonly` |
| Replies | Read and reply in the app |
| Claude's role | Writes a draft; rewrites it on instruction. No critique mode |
| Email format | Plain text, no attachments, no tracking pixels |

## What people see

**Profile page: Pitch mailbox card.**
- **No mailbox yet:** the card offers "Connect Gmail". If the artist already has connected mailboxes, it also offers a list to pick one, so two Synman profiles can share the Synman Gmail.
- **Connecting:** runs Google's consent screen, which is separate from sign-in. Google shows its "unverified app" warning once, because `gmail.readonly` is a restricted scope. Clicking through is fine for this use.
- **Connected:** the card shows "Pitching from x@gmail.com" with **Change** and **Disconnect**.
  - **Disconnect** detaches the mailbox from this profile. Once no profile uses it, Noble Hunter gives up its Google access too. Past conversations stay readable.
- **Expired or revoked:** the card, the entry's compose panel and the runner panel say "Reconnect Gmail".
- **`MAIL_TOKEN_KEY` missing:** the card explains that mail isn't configured, and nothing else about mail appears.
- **Who can act:** only the artist's members and admins can see or change the card.

**Digest entry.**
- **Button:** shown only when the best contact is an email graded A or B.
  - **"Write pitch"** when the profile has a working mailbox.
  - **"Connect a mailbox to this profile"**, linking to the card, when it doesn't.
- **Compose panel:** opens inside the card with these fields:
  - "Sending from x@gmail.com".
  - To, filled with the contact and editable.
  - A track picker, listing the profile's tracks.
  - Subject.
  - Body.
  - A "Tell Claude what to change" box.
- **First open:** Claude's first draft fills Subject and Body.
- **Drafts:** save as you type, so closing the panel or the tab loses nothing.
- **Send:** asks for confirmation ("Send to x@y.com?"). On success the panel becomes the thread and the entry is marked **Pitched**.
- **Blocked curators:** if the curator has been marked bad-fit for this artist, or dead, since the digest was built, Send is disabled and says why.

**The thread.**
- **Layout:** once a pitch is sent, the card shows the conversation oldest first. Quoted history ("On … wrote:") is folded away.
- **Replies:** a reply marks the entry **Replied**, unless it's already Placed. "Draft reply" gives a Claude draft built from the thread, with the same edit-and-instruct flow.
- **Sent from Gmail:** messages sent from Gmail in that thread appear too.

**Inbox page.**
- **Contents:** a nav link, with an unread count, to every conversation for the viewer's artists: newest activity first, unread marked, filterable by profile. Each row opens the entry's thread.
- **Admins:** see all conversations, grouped by artist.
- **"Check now":** checks the viewer's mailboxes immediately.
- **Read status:** opening a thread marks its replies read. Read status is shared by the artist's members.

## How it works

### Units

| Module | One job |
|---|---|
| `core/mail_crypto.py` | Encrypts and decrypts refresh tokens with Fernet (`MAIL_TOKEN_KEY`) |
| `core/gmail.py` | Thin Gmail API client over httpx: refresh the access token, get the profile, send, list history, get a thread. Raises `GmailError` with kinds `auth` (reconnect), `transient` and `rejected` |
| `core/mailboxes.py` | An artist's mailboxes: save on connect (or refresh the token of an existing address), attach to or detach from a profile, mark needing reconnect, disconnect |
| `core/mime.py` | Builds a plain-text message (To, Subject, In-Reply-To, References) as base64url. Parses a Gmail message into sender, date and plain-text body, with quoted history split off |
| `core/pitches.py` | Drafts, sending and status changes: save a draft, send a pitch or reply (with a one-time key), mark Pitched or Replied without downgrading |
| `core/mail_sync.py` | For one mailbox, fetches new messages in threads Noble Hunter started and stores them. Used by the runner and by "Check now" |
| `core/pitch_writer.py` | `PitchWriter` protocol and `ClaudePitchWriter` (Opus 5, structured `{subject, body}` output) |
| `core/inbox_view.py` | Read models for the thread panel and the Inbox page, including unread counts, filtered by viewer |
| `web/mail.py` | Pitch mailbox card and the OAuth connect/callback/attach/disconnect routes |
| `web/pitches.py` | Compose, draft save, Claude draft or revise, send, thread panel |
| `web/inbox.py` | Inbox page and "Check now" |
| `pipeline/worker.py` | Adds a mail-check thread |

Gmail and Claude code live in `core/` because both the web app and the runner use them. The web app still never imports `pipeline/`. Every web route goes through `core/access.py` from the artists project, so someone who isn't on the artist gets 404.

### Data (migration 0008; Artists and access uses 0006 and 0007)

- **`mail_accounts`** columns:
  - `id`
  - `artist_id`
  - `address`
  - `refresh_token_encrypted`
  - `history_id`
  - `connected_at`
  - `connected_by`
  - `disconnected_at`
  - `last_checked_at`
  - `needs_reconnect`
  - `last_error`

  `(artist_id, address)` is unique.
- **`profiles`** gains `mail_account_id` (nullable). A check stops a profile using a mailbox from another artist.
- **`email_messages`** columns:
  - `id`
  - `mail_account_id`
  - `outreach_id`
  - `gmail_message_id`
  - `gmail_thread_id`
  - `direction` (`out`/`in`)
  - `from_address`
  - `to_address`
  - `subject`
  - `body_text`
  - `quoted_text`
  - `sent_at`
  - `read_at`
  - `send_key` (unique, nullable)
  - `created_at`

  `(mail_account_id, gmail_message_id)` is unique.
- **`outreach`** gains:
  - `mail_account_id`
  - `gmail_thread_id`

  The pair is unique when set, and it records which mailbox the thread lives in, even if the profile later changes mailbox.

  It also gains draft fields:
  - `draft_subject`
  - `draft_body`
  - `draft_track_id`
  - `draft_updated_at`

  One draft per entry at a time: the pitch first, then the next reply.
- **Roles:**
  - `noble_web` gets write on `mail_accounts` and `email_messages`, the profile's `mail_account_id`, and the new `outreach` columns.
  - `noble_pipeline` gets read/update on `mail_accounts` (history and error fields), insert on `email_messages`, and update on `outreach` status.
  - Run `db grant` after migrating, and migrate Neon before pushing.

**Enforcing "same artist".** A foreign key can't compare two tables' artist ids on its own. Either give `mail_accounts` a unique `(id, artist_id)` and point a composite foreign key at it from `profiles`, or check it in `core/mailboxes.py` and test it. Prefer the composite key.

### Connecting

- **Starting the flow:** `GET /profiles/{id}/mail/connect` checks the viewer can see the profile, then builds a Google authorization URL with:
  - the two Gmail scopes;
  - `access_type=offline` and `prompt=consent`, so Google always returns a refresh token;
  - `state` (plus the profile id) stored in the session.
- **Callback:** `GET /mail/callback` does five things:
  - checks `state`;
  - exchanges the code;
  - reads the address and starting `history_id` from `users.getProfile`;
  - saves the mailbox under the profile's artist, or refreshes the token if the artist already has that address;
  - attaches it to the profile.
- **Attaching:** `POST /profiles/{id}/mail/attach` attaches one of the artist's existing mailboxes.
- **Disconnecting:** `POST /profiles/{id}/mail/disconnect` detaches the mailbox. If no profile uses it any more, it revokes the token at Google (best effort) and deletes the token. The row and its messages stay, marked disconnected, so threads remain readable.
- **Client:** it reuses the existing Google OAuth client (`GOOGLE_CLIENT_ID`/`SECRET`) with one extra redirect URI.

### Sending

`POST /outreach/{id}/send` carries To, Subject, Body, the track and a `send_key` rendered into the panel.

1. Check access and input:
   - The viewer can see the entry.
   - To is a single valid address.
   - Subject has no line breaks and is at most 200 characters.
   - Body is 1–5,000 characters.
   - The curator is eligible for this artist: not dead, not bad-fit.
   - A mailbox is available: the entry's own mailbox for replies, the profile's for a first pitch. It must be connected and not need reconnecting.
2. If the `send_key` already exists, return that message instead of sending again.
3. Build the MIME message. A reply adds `threadId` plus `In-Reply-To`/`References` from the latest message.
4. Send through Gmail and store the outbound row.
   - On the first send, set `outreach.mail_account_id` and `gmail_thread_id`.
   - Clear the draft and mark Pitched (from `new` or `skip`; `pitched_at` is set once).
5. On `GmailError`:
   - Keep the draft and show the reason inline.
   - If the kind is `auth`, mark the mailbox as needing reconnect.

### Checking for replies

`sync_mailbox(session, gmail, mailbox, now)`:

1. Call `history.list` from the mailbox's `history_id` for `messageAdded`.
2. Keep only messages whose thread is an outreach thread in this mailbox. Skip ones already stored.
3. Fetch and parse each message:
   - From the mailbox's own address: store it as `out` (sent from Gmail).
   - Otherwise: store it as `in`, unread.
4. Mark entries with a new inbound message as Replied (from `new` or `pitched`).
5. Save the newest `history_id` and `last_checked_at`.
6. If Gmail says the history id is too old (404), fetch each of this mailbox's threads from the last 90 days with `threads.get` instead, then reset the history id from Gmail's `users.getProfile`.

**The runner** loops over every connected mailbox that doesn't need reconnecting, every 2 minutes. It does this from its own thread with its own session, so a 40-minute nightly run doesn't delay replies. Each mailbox is handled on its own:
- An `auth` error marks that mailbox as needing reconnect.
- A transient error is logged and retried next cycle.
- Neither stops the others.

**"Check now"** runs the same function for each of the viewer's artists' mailboxes, from Vercel.

**Warnings.** The runner panel gains two, each shown only to people who can see the affected artist:
- a mailbox needs reconnecting;
- replies haven't been checked for 15 minutes while any mailbox is connected.

### Claude's drafts

`ClaudePitchWriter.draft(request) -> Draft(subject, body, spend_usd)`:
- **Model and settings:** `claude-opus-5`, thinking disabled, low effort, JSON-schema output, 60 s timeout.
- **The request includes:**
  - the profile (artist name, genres, reference artists);
  - the chosen track (title, Spotify link, notes);
  - the playlist (name, description, followers, the reference artists found on it);
  - the curator's name, plus the brief and angle;
  - optionally, the current draft and an instruction (at most 500 characters);
  - optionally, the thread, when drafting a reply.
- **Prompt rules:**
  - Under about 120 words, first person as the artist.
  - Name the playlist and why the track suits it.
  - One link.
  - No flattery templates, no hype, no mention of payment.
  - Never invent facts about the curator or the track.
- **Failure:** `PitchWriterError` shows "Claude couldn't write a draft; you can still write it yourself". The panel still works.

About 1–2¢ a draft, on the shared Anthropic key. It isn't counted against the nightly pipeline budget, same as Ask Claude.

### Security

- **Tokens:** refresh tokens are encrypted at rest. Access tokens live in memory only.
- **Scopes:** the two Gmail scopes, nothing broader.
- **Access:** every mail route checks the viewer against the artist.
- **Input:** every send is a CSRF-protected POST. Header fields are checked against injection.
- **Display:** message bodies render as escaped text, never HTML. Links inside them aren't made clickable.
- **Logs:** tokens are never logged, and neither are message bodies.
- **Google's limit:** an unverified app can have up to 100 people grant a restricted scope. Past that, Google's security assessment is needed. The People page shows how many mailboxes are connected.

## Testing

TDD against the Docker Postgres, with `FakeGmail` and `FakePitchWriter`. No test sends real mail.

- **Crypto:** round trip, a wrong key fails loudly, a missing key means mail isn't configured.
- **MIME:** reply headers, header-injection rejection, parsing plain and HTML-only messages, splitting off quoted history.
- **Mailboxes:**
  - connecting creates or refreshes;
  - attach within an artist;
  - attaching another artist's mailbox is refused, by the database too;
  - disconnect keeps messages;
  - a shared mailbox is revoked only when its last profile lets go.
- **Pitches:**
  - draft save, and send marks Pitched;
  - the one-time key prevents a double send;
  - a bad-fit or dead curator is refused;
  - a failed send keeps the draft, and an `auth` error marks reconnect;
  - replies use the entry's mailbox even after the profile changes mailbox;
  - no status downgrade.
- **Sync:**
  - a new reply marks Replied and is unread;
  - messages sent from Gmail are stored as `out`;
  - unrelated threads are ignored;
  - the expired-history fallback works;
  - duplicates are skipped;
  - one mailbox's auth error doesn't stop the next.
- **Worker:** the mail-check thread runs during a long run, skips mailboxes that need reconnecting, and covers every mailbox.
- **Web:**
  - the connect flow (state mismatch refused), attach and disconnect;
  - the compose panel shows only for email contacts;
  - send, the thread panel, and the Inbox with profile filter and unread counts;
  - "Check now" and the warnings;
  - every new route is added to the access route walk.
- **Writer:** request contents, parsing, errors. There's one live test, skipped unless enabled.

## Build order

Starts after Artists and access is complete.

1. **Connect mailboxes.** Crypto, Gmail auth, the `mail_accounts` table, the profile card (connect, attach, disconnect).
2. **Compose and send.** Drafts, Claude writer, MIME, send, Pitched.
3. **Replies arrive.** Per-mailbox sync, the runner thread, the thread panel, Replied, warnings.
4. **Reply in-app and the Inbox.** Reply drafts, the Inbox page, unread counts, "Check now".

Each stage is pushed only after Neon is migrated.

## Setup (once, after stage 1 is deployed)

1. **Create the Synman Gmail.**
2. **Google Cloud,** in the project used for sign-in:
   - enable the Gmail API;
   - add the `gmail.send` and `gmail.readonly` scopes to the consent screen;
   - add `https://<app>/mail/callback` as a redirect URI;
   - make sure the publishing status is **In production**, so tokens don't expire after 7 days. The artists project already asks for this.
3. **Secrets:**
   - Add `MAIL_TOKEN_KEY` (generated into `.env.local`) to Vercel.
   - Make sure `.env.local` on the Mac Mini has `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`.
4. **Connect:** open a Synman profile → Pitch mailbox → Connect Gmail, signed in as the Synman account. Attach it to the other Synman profiles.

## Out of scope

- Attachments and HTML email.
- Open or click tracking.
- Follow-up reminders.
- Bulk or scheduled sending.
- Per-person read status.
- Instagram or other DMs.
- Sending to contacts graded C.
