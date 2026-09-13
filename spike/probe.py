"""Stage 0 probe: load one playlist in the web player and record its internal API calls.

Throwaway. Answers one question: which captured responses carry added dates,
followers, description, owner, and the full (paginated) track list?
"""

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Response, async_playwright

CAPTURE_HOSTS = ("api-partner.spotify.com", "spclient.spotify.com", "spclient.wg.spotify.com", "api.spotify.com")


def operation_name(response: Response) -> str:
    query = parse_qs(urlparse(response.url).query)
    if "operationName" in query:
        return query["operationName"][0]
    try:
        body = response.request.post_data_json or {}
        return body.get("operationName", "unknown")
    except Exception:
        return "unknown"


async def probe(target: str) -> None:
    """`target` is a playlist ID, or a path like `user/<id>`."""
    path_part = target if "/" in target else f"playlist/{target}"
    out_dir = Path("captures") / path_part.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    counter = 0

    async def on_response(response: Response) -> None:
        nonlocal counter
        if not any(host in response.url for host in CAPTURE_HOSTS):
            return
        try:
            body = await response.json()
        except Exception:
            return
        counter += 1
        name = operation_name(response)
        path = out_dir / f"{counter:02d}_{name}.json"
        path.write_text(json.dumps(body, indent=2))
        request_body = response.request.post_data
        if request_body:
            (out_dir / f"{counter:02d}_{name}.request.json").write_text(request_body)
        print(f"[{response.status}] {name:<30} {len(path.read_text()):>8} bytes  {response.url[:90]}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="en-US")
        page.on("response", on_response)
        await page.goto(f"https://open.spotify.com/{path_part}", wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)
        await page.screenshot(path=str(out_dir / "screenshot_top.png"))

        # Scroll the track list (not the sidebar) to trigger pagination.
        await page.mouse.move(760, 500)
        for _ in range(15):
            await page.mouse.wheel(0, 4000)
            await page.wait_for_timeout(1200)

        await page.screenshot(path=str(out_dir / "screenshot.png"))
        await browser.close()

    print(f"captured {counter} responses -> {out_dir}")


if __name__ == "__main__":
    asyncio.run(probe(sys.argv[1]))
