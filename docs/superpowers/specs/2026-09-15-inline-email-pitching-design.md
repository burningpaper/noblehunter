# Inline email pitching: design

**Date:** 2026-09-15
**Status:** Approved in conversation; awaiting review of this write-up.

## Why

The digest tells Jarred who to pitch and why. The pitching itself happens elsewhere: he copies an address, opens Gmail, writes from scratch, sends, then comes back to click "Pitched". Replies land in a personal inbox, get mixed in with everything else, and never make it back to Noble Hunter.

This feature closes that loop for email contacts. Jarred writes the pitch inside the digest entry with a Claude draft to start from, sends it from a dedicated Synman Gmail, and sees the curator's reply under the same playlist. He can answer from there too.

It changes one line of `spec.md`, which said "nothing in this spec sends messages". It still holds for everything automatic. Noble Hunter never sends on its own: every message goes out because Jarred pressed Send.

Instagram stays manual. Meta's API only lets a business reply to someone who wrote first, and the unofficial tools log in with the account's password and risk it being disabled.

## Decisions

| Question | Decision |
|---|---|
| Mailbox | A separate Synman Gmail, one per installation, connected once |
| Connection | Gmail API with OAuth scopes `gmail.send` and `gmail.readonly` |
| Replies | Read and reply in the app |
| Claude's role | Writes a draft; rewrites it on instruction. No critique mode |
| Email format | Plain text, no attachments, no tracking pixels |

## What Jarred sees

**Pitch mailbox card.** It sits on the Profiles page beside the Claude budget card, where the app's settings already live. Its routes share the budget card's `/settings` prefix.
- **Not connected:** the card says so and offers "Connect Gmail". That runs Google's consent screen, separate from sign-in. Google shows its "unverified app" warning once, because `gmail.readonly` is a restricted scope. Clicking through is fine for personal use.
- **Connected:** the card shows the connected address and a Disconnect button.
- **Expired or revoked:** the card and the runner panel both say "Reconnect Gmail".
- **`MAIL_TOKEN_KEY` missing:** the card explains that mail isn't configured, and nothing else about mail appears.

**Digest entry.**
- **"Write pitch" button:** appears only when the best contact is an email graded A or B and a mailbox is connected.
- **Compose panel:** opens inside the card with these fields:
  - To, filled with the contact and editable.
  - A track picker, listing the profile's tracks.
  - Subject.
  - Body.
  - A "Tell Claude what to change" box.
- **First open:** Claude's first draft fills Subject and Body.
- **Drafts:** save as he types, so closing the panel or the tab loses nothing.
- **Send:** asks for confirmation ("Send to x@y.com?"). On success the panel becomes the thread and the entry is marked **Pitched**.
- **Blocked curators:** if the curator has been marked bad-fit or dead since the digest was built, Send is disabled and says why.

**The thread.**
- **Layout:** once a pitch is sent, the card shows the conversation oldest first. Quoted history ("On … wrote:") is folded away.
- **Replies:** a reply marks the entry **Replied**, unless it's already Placed. A "Draft reply" button gives a Claude draft built from the thread, with the same edit-and-instruct flow.
- **Sent from Gmail:** messages Jarred sends from Gmail in that thread appear too.

**Inbox page.**
- **Contents:** a nav link, with an unread count, to a list of every conversation: newest activity first, unread marked, each row opening the entry's thread.
- **"Check now":** looks for replies immediately.
- **Read status:** opening a thread marks its replies read.

## How it works

### Units

| Module | One job |
|---|---|
| `core/mail_crypto.py` | Encrypts and decrypts the refresh token with Fernet (`MAIL_TOKEN_KEY`) |
| `core/gmail.py` | Thin Gmail API client over httpx: refresh the access token, get the profile, send, list history, get a thread. Raises `GmailError` with kinds `auth` (reconnect), `transient` and `rejected` |
| `core/mail_account.py` | The single connected account: save on connect, load, mark needing reconnect, disconnect |
| `core/mime.py` | Builds a plain-text message (To, Subject, In-Reply-To, References) as base64url. Parses a Gmail message into sender, date and plain-text body, with quoted history split off |
| `core/pitches.py` | Drafts, sending and status changes: save a draft, send a pitch or reply (with a one-time key), mark Pitched or Replied without downgrading |
| `core/mail_sync.py` | Fetches new messages in threads Noble Hunter started and stores them. Used by the runner and by "Check now" |
| `core/pitch_writer.py` | `PitchWriter` protocol and `ClaudePitchWriter` (Opus 5, structured `{subject, body}` output) |
| `core/inbox_view.py` | Read models for the thread panel and the Inbox page, including unread counts |
| `web/mail.py` | Pitch mailbox card and the OAuth connect/callback/disconnect routes |
| `web/pitches.py` | Compose, draft save, Claude draft or revise, send, thread panel |
| `web/inbox.py` | Inbox page and "Check now" |
| `pipeline/worker.py` | Adds a mail-check thread |

Gmail and Claude code live in `core/` because both the web app and the runner use them. The web app still never imports `pipeline/`.

### Data (migration 0006)

- **`mail_account`** holds at most one row, enforced by a unique constant column. Its columns:
  - `address`
  - `refresh_token_encrypted`
  - `history_id`
  - `connected_at`
  - `last_checked_at`
  - `needs_reconnect`
  - `last_error`
- **`email_messages`** columns:
  - `id`
  - `outreach_id`
  - `gmail_message_id` (unique)
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
- **`outreach`** gains:
  - `gmail_thread_id` (unique, nullable)
  - `draft_subject`
  - `draft_body`
  - `draft_track_id`
  - `draft_updated_at`

  One draft per entry at a time: the pitch first, then the next reply.
- **Roles:**
  - `noble_web` gets write on `mail_account`, `email_messages` and the new `outreach` columns.
  - `noble_pipeline` gets read/update on `mail_account` (history and error fields), insert on `email_messages`, and update on `outreach` status.
  - Run `db grant` after migrating, and migrate Neon before pushing.

### Connecting

- **Starting the flow:** `GET /settings/mail/connect` builds a Google authorization URL with:
  - the two Gmail scopes;
  - `access_type=offline` and `prompt=consent`, so Google always returns a refresh token;
  - `state` stored in the session.
- **Callback:** `GET /settings/mail/callback` does four things:
  - checks `state`;
  - exchanges the code;
  - reads the address from `users.getProfile`, which also gives the starting `history_id`;
  - encrypts and stores the token.
- **Client:** it reuses the existing Google OAuth client (`GOOGLE_CLIENT_ID`/`SECRET`) with one extra redirect URI.
- **Disconnecting:** revokes the token at Google (best effort) and deletes the row. Stored messages stay.

### Sending

`POST /outreach/{id}/send` carries To, Subject, Body, the track and a `send_key` rendered into the panel.

1. Check the input:
   - To must be a single valid address.
   - Subject has no line breaks and is at most 200 characters.
   - Body is 1–5,000 characters.
   - The curator is not blocked.
2. If the `send_key` already exists, return that message instead of sending again.
3. Build the MIME message. A reply adds `threadId` plus `In-Reply-To`/`References` from the latest message.
4. Send through Gmail, store the outbound row, set `outreach.gmail_thread_id` on the first send, clear the draft, and mark Pitched (from `new` or `skip`; `pitched_at` is set once).
5. On `GmailError`:
   - Keep the draft and show the reason inline.
   - If the kind is `auth`, mark the account as needing reconnect.

### Checking for replies

`sync_mail(session, gmail, now)`:

1. Call `history.list` from `mail_account.history_id` for `messageAdded`.
2. Keep only messages whose thread id belongs to an outreach entry. Skip ones already stored.
3. Fetch and parse each message:
   - From the connected address: store it as `out` (sent from Gmail).
   - Otherwise: store it as `in`, unread.
4. Mark entries with a new inbound message as Replied (from `new` or `pitched`).
5. Save the newest `history_id` and `last_checked_at`.
6. If Gmail says the history id is too old (404), fetch each thread from the last 90 days with `threads.get` instead, then reset the history id from the profile.

**The runner** calls this every 2 minutes from its own thread with its own session, so a 40-minute nightly run doesn't delay replies. An `auth` error marks the account as needing reconnect, and checks pause until it's reconnected. Transient errors are logged and retried next cycle.

**"Check now"** calls the same function from Vercel.

**Warnings:** the runner panel gains two:
- the mailbox needs reconnecting;
- replies haven't been checked for 15 minutes while a mailbox is connected.

### Claude's drafts

`ClaudePitchWriter.draft(request) -> Draft(subject, body, spend_usd)`:
- **Model and settings:** `claude-opus-5`, thinking disabled, low effort, JSON-schema output, 60 s timeout.
- **The request includes:**
  - the profile (name, genres, reference artists);
  - the chosen track (title, Spotify link, notes);
  - the playlist (name, description, followers, the reference artists found on it);
  - the curator's name, plus the brief and angle;
  - optionally, the current draft and an instruction (at most 500 characters);
  - optionally, the thread, when drafting a reply.
- **Prompt rules:**
  - Under about 120 words, first person as Synman.
  - Name the playlist and why the track suits it.
  - One link.
  - No flattery templates, no hype, no mention of payment.
  - Never invent facts about the curator or the track.
- **Failure:** `PitchWriterError` shows "Claude couldn't write a draft; you can still write it yourself". The panel still works.

About 1–2¢ a draft. It isn't counted against the nightly pipeline budget, same as Ask Claude.

### Security

- **Token:** the refresh token is encrypted at rest. Access tokens live in memory only.
- **Scopes:** the two Gmail scopes, nothing broader.
- **Input:** every send is a CSRF-protected POST from a signed-in, allow-listed user. Header fields are checked against injection.
- **Display:** message bodies render as escaped text, never HTML. Links inside them aren't made clickable.
- **Logs:** tokens are never logged, and neither are message bodies.

## Testing

TDD against the Docker Postgres, with `FakeGmail` and `FakePitchWriter`. No test sends real mail.

- **Crypto:** round trip, a wrong key fails loudly, a missing key means mail isn't configured.
- **MIME:** reply headers, header-injection rejection, parsing plain and HTML-only messages, splitting off quoted history.
- **Pitches:** draft save, send marks Pitched, the one-time key prevents a double send, a blocked curator is refused, a failed send keeps the draft, an `auth` error marks reconnect, no status downgrade.
- **Sync:** a new reply marks Replied and is unread, messages Jarred sent from Gmail are stored as `out`, unrelated threads are ignored, the expired-history fallback works, duplicates are skipped.
- **Worker:** the mail-check thread runs during a long run and pauses when the account needs reconnecting.
- **Web:**
  - the connect flow (state mismatch refused) and disconnect;
  - the compose panel shows only for email contacts;
  - send, the thread panel, the Inbox and unread counts;
  - "Check now" and the runner panel warnings.
- **Writer:** request contents, parsing, errors. There's one live test, skipped unless enabled.

## Build order

1. **Connect the mailbox.** Crypto, Gmail auth, the `mail_account` table, the Pitch mailbox card.
2. **Compose and send.** Drafts, Claude writer, MIME, send, Pitched.
3. **Replies arrive.** Sync, the runner thread, the thread panel, Replied, warnings.
4. **Reply in-app and the Inbox.** Reply drafts, the Inbox page, unread counts, "Check now".

Each stage is pushed only after Neon is migrated.

## Jarred's setup (once, after stage 1 is deployed)

1. **Create the Synman Gmail.**
2. **Google Cloud,** in the project used for sign-in:
   - enable the Gmail API;
   - add the `gmail.send` and `gmail.readonly` scopes to the consent screen;
   - add `https://<app>/settings/mail/callback` as a redirect URI;
   - set the publishing status to **In production**, so tokens don't expire after 7 days.
3. **Secrets:**
   - Add `MAIL_TOKEN_KEY` (generated into `.env.local`) to Vercel.
   - Make sure `.env.local` on the Mac Mini has `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`.
4. **Connect:** Profiles page → Pitch mailbox → Connect Gmail, signed in as the Synman account.

## Out of scope

- Attachments and HTML email.
- Open or click tracking.
- Follow-up reminders.
- Bulk or scheduled sending.
- More than one mailbox.
- Instagram or other DMs.
- Sending to contacts graded C.
