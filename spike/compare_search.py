"""Stage 0 spike: compare Serper (Google) and Brave for discovering Spotify playlists.

Throwaway. For each seed, asks both providers for ~30 results restricted to
open.spotify.com/playlist, then reports unique playlist IDs per provider and overlap.
Keys come from the environment (or spike/.env), never from code.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

SEEDS = ["IDM", "braindance", "glitchy ambient", "late-night electronics", "Boards of Canada playlist"]
TARGET_RESULTS = 30
PLAYLIST_ID = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?playlist/([A-Za-z0-9]{22})")


def load_env_file() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def playlist_ids(urls: list[str]) -> list[str]:
    ids: list[str] = []
    for url in urls:
        match = PLAYLIST_ID.search(url)
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def search_serper(client: httpx.Client, key: str, query: str) -> list[str]:
    urls: list[str] = []
    for page in (1, 2, 3):
        response = client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key},
            json={"q": query, "num": 10, "page": page},
        )
        response.raise_for_status()
        page_urls = [r["link"] for r in response.json().get("organic", [])]
        print(f"    serper page={page} results={len(page_urls)}")
        urls += page_urls
        time.sleep(0.5)
    return urls[:TARGET_RESULTS]


def search_brave(client: httpx.Client, key: str, query: str) -> list[str]:
    urls: list[str] = []
    for offset in (0, 1):
        response = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            params={"q": query, "count": 20, "offset": offset},
        )
        response.raise_for_status()
        page_urls = [r["url"] for r in response.json().get("web", {}).get("results", [])]
        print(f"    brave  offset={offset} results={len(page_urls)}")
        urls += page_urls
        time.sleep(1.1)  # Brave free tier allows 1 request per second.
    return urls[:TARGET_RESULTS]


def main() -> None:
    load_env_file()
    serper_key, brave_key = os.environ.get("SERPER_API_KEY"), os.environ.get("BRAVE_API_KEY")
    missing = [name for name, key in (("SERPER_API_KEY", serper_key), ("BRAVE_API_KEY", brave_key)) if not key]
    if missing:
        sys.exit(f"Missing {', '.join(missing)}. Add them to spike/.env (see .env.example).")

    report = {}
    with httpx.Client(timeout=20) as client:
        for seed in SEEDS:
            query = f"site:open.spotify.com/playlist {seed}"
            serper = playlist_ids(search_serper(client, serper_key, query))
            brave = playlist_ids(search_brave(client, brave_key, query))
            both = set(serper) & set(brave)
            report[seed] = {"serper": serper, "brave": brave}
            print(
                f"{seed:<28} serper={len(serper):>2}  brave={len(brave):>2}  "
                f"overlap={len(both):>2}  union={len(set(serper) | set(brave)):>2}"
            )

    Path("search_comparison.json").write_text(json.dumps(report, indent=2))
    total_serper = len({i for r in report.values() for i in r["serper"]})
    total_brave = len({i for r in report.values() for i in r["brave"]})
    print(f"\nunique playlists across all seeds: serper={total_serper}  brave={total_brave}")


if __name__ == "__main__":
    main()
