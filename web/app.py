"""Noble Hunter web app entrypoint.

For now this is a placeholder that proves the Vercel deploy and the Neon link work.
The real settings UI arrives in Stage 2.
"""

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# Env vars the Vercel Neon integration may set. We only report whether one exists, never its value.
DATABASE_URL_VARS = ("DATABASE_URL", "POSTGRES_URL")

app = FastAPI(title="Noble Hunter", docs_url=None, redoc_url=None, openapi_url=None)


def database_configured() -> bool:
    return any(os.environ.get(name) for name in DATABASE_URL_VARS)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "database_configured": database_configured()}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Noble Hunter</title>
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           font-family: system-ui, sans-serif; background: #0f1115; color: #e8e8ea; }
    main { text-align: center; padding: 2rem; }
    h1 { font-size: clamp(2rem, 6vw, 3.5rem); margin: 0 0 .5rem; letter-spacing: -.02em; }
    p { color: #9a9ca5; margin: 0; }
  </style>
</head>
<body>
  <main>
    <h1>Noble Hunter</h1>
    <p>Deployed. The settings app is on its way.</p>
  </main>
</body>
</html>"""
