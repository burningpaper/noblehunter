"""Stage 0 spike: fetch complete playlist data for a list of playlist IDs.

Throwaway. Loads each playlist once in headless Chromium (no login), captures the
web player's own `fetchPlaylist` request, then pages through the track list by
replaying `fetchPlaylistContents` with explicit offsets. Scroll-driven paging was
tried first and silently skipped tracks, so we don't rely on it.
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Page, Request, async_playwright

PATHFINDER_URL = "https://api-partner.spotify.com/pathfinder/v2/query"
PAGE_SIZE = 50
DELAY_BETWEEN_PAGES_S = 1.0
DELAY_BETWEEN_PLAYLISTS_S = 4.0
FORWARDED_HEADERS = ("authorization", "client-token", "app-platform", "spotify-app-version", "content-type")
FIXTURE_DIR = Path("fixtures")


async def capture_first_query(page: Page, playlist_id: str) -> tuple[dict, dict, dict]:
    """Load the playlist page and return (headers, request body, response body) of fetchPlaylist."""
    loop = asyncio.get_running_loop()
    captured: asyncio.Future[Request] = loop.create_future()

    def on_request(request: Request) -> None:
        if request.url.startswith(PATHFINDER_URL) and not captured.done():
            body = request.post_data_json or {}
            if body.get("operationName") == "fetchPlaylist":
                captured.set_result(request)

    page.on("request", on_request)
    await page.goto(f"https://open.spotify.com/playlist/{playlist_id}", wait_until="domcontentloaded")
    request = await asyncio.wait_for(captured, timeout=30)
    response = await request.response()
    if response is None or response.status != 200:
        raise RuntimeError(f"fetchPlaylist failed for {playlist_id}: status {response and response.status}")

    all_headers = await request.all_headers()
    headers = {k: v for k, v in all_headers.items() if k in FORWARDED_HEADERS}
    return headers, request.post_data_json, await response.json()


async def fetch_remaining_items(page: Page, headers: dict, first_request: dict, total: int, start: int) -> list[dict]:
    items: list[dict] = []
    for offset in range(start, total, PAGE_SIZE):
        await asyncio.sleep(DELAY_BETWEEN_PAGES_S)
        body = {
            "variables": {**first_request["variables"], "offset": offset, "limit": PAGE_SIZE},
            "operationName": "fetchPlaylistContents",
            "extensions": first_request["extensions"],
        }
        body["variables"].pop("enableWatchFeedEntrypoint", None)
        response = await page.request.post(PATHFINDER_URL, headers=headers, data=json.dumps(body))
        if response.status != 200:
            raise RuntimeError(f"fetchPlaylistContents offset={offset} failed: status {response.status}")
        payload = await response.json()
        if payload.get("errors"):
            raise RuntimeError(f"fetchPlaylistContents offset={offset} errors: {payload['errors']}")
        items.extend(payload["data"]["playlistV2"]["content"]["items"])
    return items


def summarise(playlist: dict, items: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    dates = sorted(
        datetime.fromisoformat(i["addedAt"]["isoString"].replace("Z", "+00:00"))
        for i in items
        if i.get("addedAt")
    )
    newest = dates[-1] if dates else None
    total = playlist["content"]["totalCount"]
    owner = playlist["ownerV2"]["data"]
    return {
        "name": playlist["name"],
        "owner": owner.get("name"),
        "owner_id": owner.get("username"),
        "followers": playlist.get("followers"),
        "description_chars": len(playlist.get("description") or ""),
        "total": total,
        "unique_items": len({i["uid"] for i in items}),
        "with_added_at": len(dates),
        "days_since_last_add": (now - newest).days if newest else None,
        "adds_last_180d": sum(1 for d in dates if (now - d).days <= 180),
    }


async def fetch_playlist(browser, playlist_id: str) -> dict:
    context = await browser.new_context(locale="en-US")
    page = await context.new_page()
    try:
        headers, first_request, first_response = await capture_first_query(page, playlist_id)
        playlist = first_response["data"]["playlistV2"]
        items = list(playlist["content"]["items"])
        items += await fetch_remaining_items(
            page, headers, first_request, playlist["content"]["totalCount"], start=len(items)
        )
        FIXTURE_DIR.mkdir(exist_ok=True)
        fixture = {"playlist": {**playlist, "content": {**playlist["content"], "items": items}}}
        (FIXTURE_DIR / f"{playlist_id}.json").write_text(json.dumps(fixture, indent=2))
        return summarise(playlist, items)
    finally:
        await context.close()


async def main(playlist_ids: list[str]) -> None:
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for index, playlist_id in enumerate(playlist_ids):
            if index:
                await asyncio.sleep(DELAY_BETWEEN_PLAYLISTS_S)
            started = time.monotonic()
            try:
                summary = await fetch_playlist(browser, playlist_id)
                summary["seconds"] = round(time.monotonic() - started, 1)
                complete = summary["unique_items"] == summary["total"] == summary["with_added_at"]
                summary["complete"] = complete
                print(f"{'OK  ' if complete else 'GAP '} {playlist_id} {json.dumps(summary, ensure_ascii=False)}")
            except Exception as error:
                summary = {"error": f"{type(error).__name__}: {error}"}
                print(f"FAIL {playlist_id} {summary['error']}")
            results[playlist_id] = summary
        await browser.close()

    Path("results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    ok = sum(1 for r in results.values() if r.get("complete"))
    print(f"\n{ok}/{len(playlist_ids)} playlists fetched completely")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
