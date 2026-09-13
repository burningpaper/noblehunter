"""Stage 0 spike: which search provider finds playlists worth pitching?

Throwaway. Samples playlists found only by Serper, only by Brave, and by both
(from search_comparison.json), fetches each, and scores them on the spec's Alive
rule plus a stand-in reference-artist overlap.

To keep big playlists cheap, it reads the first page plus the LAST `TAIL_TRACKS`
tracks, where recent adds usually sit. Those playlists are flagged `partial`.
"""

import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from fetch_playlists import PAGE_SIZE, PATHFINDER_URL, capture_first_query

SAMPLE_PER_GROUP = 30
TAIL_TRACKS = 300
DELAY_BETWEEN_PLAYLISTS_S = 4.0
DELAY_BETWEEN_PAGES_S = 1.0
ALIVE_MAX_DAYS = 60
ALIVE_MIN_ADDS_180D = 3

# Stand-in until Jarred's artist profile exists. Lowercase for matching.
REFERENCE_ARTISTS = {
    "aphex twin", "boards of canada", "autechre", "squarepusher", "plaid", "µ-ziq",
    "venetian snares", "bogdan raczynski", "luke vibert", "ochre", "proem", "arovane",
}


async def fetch_tail(page, headers: dict, first_request: dict, total: int, already: int) -> list[dict]:
    items: list[dict] = []
    start = max(already, total - TAIL_TRACKS)
    for offset in range(start, total, PAGE_SIZE):
        await asyncio.sleep(DELAY_BETWEEN_PAGES_S)
        variables = {**first_request["variables"], "offset": offset, "limit": PAGE_SIZE}
        variables.pop("enableWatchFeedEntrypoint", None)
        body = {"variables": variables, "operationName": "fetchPlaylistContents", "extensions": first_request["extensions"]}
        response = await page.request.post(PATHFINDER_URL, headers=headers, data=json.dumps(body))
        if response.status != 200:
            raise RuntimeError(f"offset={offset} status {response.status}")
        items.extend((await response.json())["data"]["playlistV2"]["content"]["items"])
    return items


def artist_names(item: dict) -> list[str]:
    data = (item.get("itemV2") or {}).get("data") or {}
    return [a["profile"]["name"].lower() for a in (data.get("artists") or {}).get("items", [])]


def score(playlist: dict, items: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    dates = [
        datetime.fromisoformat(i["addedAt"]["isoString"].replace("Z", "+00:00"))
        for i in items
        if i.get("addedAt")
    ]
    days_since = (now - max(dates)).days if dates else None
    adds_180d = sum(1 for d in dates if (now - d).days <= 180)
    present = sorted({name for i in items for name in artist_names(i)} & REFERENCE_ARTISTS)
    total = playlist["content"]["totalCount"]
    return {
        "name": playlist["name"],
        "owner_id": playlist["ownerV2"]["data"].get("username"),
        "followers": playlist.get("followers"),
        "total": total,
        "partial": len(items) < total,
        "days_since_last_add": days_since,
        "alive": days_since is not None and days_since <= ALIVE_MAX_DAYS and adds_180d >= ALIVE_MIN_ADDS_180D,
        "reference_artists": present,
    }


def build_sample() -> dict[str, list[str]]:
    report = json.loads(Path("search_comparison.json").read_text())
    serper = {i for r in report.values() for i in r["serper"]}
    brave = {i for r in report.values() for i in r["brave"]}
    rng = random.Random(7)
    return {
        "serper_only": rng.sample(sorted(serper - brave), min(SAMPLE_PER_GROUP, len(serper - brave))),
        "brave_only": rng.sample(sorted(brave - serper), min(SAMPLE_PER_GROUP, len(brave - serper))),
        "both": sorted(serper & brave),
    }


async def main() -> None:
    sample = build_sample()
    results: dict[str, dict] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for group, ids in sample.items():
            for playlist_id in ids:
                context = await browser.new_context(locale="en-US")
                page = await context.new_page()
                try:
                    headers, first_request, first_response = await capture_first_query(page, playlist_id)
                    playlist = first_response["data"]["playlistV2"]
                    items = list(playlist["content"]["items"])
                    items += await fetch_tail(page, headers, first_request, playlist["content"]["totalCount"], len(items))
                    results[playlist_id] = {"group": group, **score(playlist, items)}
                    r = results[playlist_id]
                    print(f"{group:<12} {'ALIVE' if r['alive'] else 'dead '} refs={len(r['reference_artists'])} "
                          f"owner={r['owner_id']} {r['name'][:60]}", flush=True)
                except Exception as error:
                    results[playlist_id] = {"group": group, "error": f"{type(error).__name__}: {error}"}
                    print(f"{group:<12} FAIL  {playlist_id} {results[playlist_id]['error']}", flush=True)
                finally:
                    await context.close()
                await asyncio.sleep(DELAY_BETWEEN_PLAYLISTS_S)
        await browser.close()

    Path("search_quality.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print("\ngroup        fetched  alive  refs>=1  alive&refs>=1  spotify-owned  errors")
    for group in sample:
        rows = [r for r in results.values() if r["group"] == group]
        ok = [r for r in rows if "error" not in r]
        print(f"{group:<12} {len(ok):>7}  {sum(r['alive'] for r in ok):>5}  "
              f"{sum(bool(r['reference_artists']) for r in ok):>7}  "
              f"{sum(r['alive'] and bool(r['reference_artists']) for r in ok):>13}  "
              f"{sum(r['owner_id'] == 'spotify' for r in ok):>13}  {len(rows) - len(ok):>6}")


if __name__ == "__main__":
    asyncio.run(main())
