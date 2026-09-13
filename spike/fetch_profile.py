"""Stage 0 spike: list a curator's public playlists from their Spotify profile.

Throwaway. Loads the profile page once (no login) to capture the web player's
auth headers, then pages through `user-profile-view/v3/profile/<id>/playlists`.
The main profile call caps out around 400 playlists and ignores offsets; the
`/playlists` sub-endpoint (behind "Show all") pages properly.
"""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import Request, async_playwright

PROFILE_ENDPOINT = "spclient.wg.spotify.com/user-profile-view/v3/profile/"
FORWARDED_HEADERS = ("authorization", "client-token", "app-platform", "spotify-app-version")
PAGE_SIZE = 200
DELAY_BETWEEN_PAGES_S = 1.0


async def capture_profile_request(page, user_id: str) -> Request:
    loop = asyncio.get_running_loop()
    captured: asyncio.Future[Request] = loop.create_future()

    def on_request(request: Request) -> None:
        if PROFILE_ENDPOINT in request.url and not captured.done():
            captured.set_result(request)

    page.on("request", on_request)
    await page.goto(f"https://open.spotify.com/user/{user_id}", wait_until="domcontentloaded")
    return await asyncio.wait_for(captured, timeout=30)


async def fetch_all_playlists(page, headers: dict, user_id: str) -> list[dict]:
    playlists: list[dict] = []
    offset = 0
    while True:
        url = f"https://{PROFILE_ENDPOINT}{user_id}/playlists?offset={offset}&limit={PAGE_SIZE}&market=from_token"
        response = await page.request.get(url, headers=headers)
        if response.status != 200:
            raise RuntimeError(f"playlists offset={offset} failed: status {response.status}")
        batch = json.loads(await response.text()).get("public_playlists", [])
        # The server may return fewer than PAGE_SIZE mid-list, so only an empty page means done.
        if not batch:
            return playlists
        playlists.extend(batch)
        offset += len(batch)
        await asyncio.sleep(DELAY_BETWEEN_PAGES_S)


async def fetch_profile(user_id: str) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="en-US")
        request = await capture_profile_request(page, user_id)
        headers = {k: v for k, v in (await request.all_headers()).items() if k in FORWARDED_HEADERS}
        # The player asks for protobuf; the same endpoints serve JSON when asked.
        headers["accept"] = "application/json"

        summary_response = await page.request.get(request.url, headers=headers)
        profile = json.loads(await summary_response.text())
        playlists = await fetch_all_playlists(page, headers, user_id)
        await browser.close()

    out = Path("captures") / f"profile_{user_id}_all.json"
    out.write_text(json.dumps({"profile": profile, "playlists": playlists}, indent=2, ensure_ascii=False))

    owner_uri = f"spotify:user:{user_id}"
    owned = [pl for pl in playlists if pl.get("owner_uri") == owner_uri]
    with_followers = sum(1 for pl in playlists if "followers_count" in pl)
    print(f"name: {profile.get('name')} | total_public_playlists_count: {profile.get('total_public_playlists_count')}")
    print(f"paged playlists: {len(playlists)} (unique {len({pl['uri'] for pl in playlists})})")
    print(f"owned by {user_id}: {len(owned)} | owned by others: {len(playlists) - len(owned)}")
    print(f"items carrying followers_count: {with_followers} of {len(playlists)}")
    print("item keys seen:", sorted({key for pl in playlists for key in pl}))


if __name__ == "__main__":
    asyncio.run(fetch_profile(sys.argv[1]))
