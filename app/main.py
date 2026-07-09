"""FastAPI web server for dc-release-notes-monitor-web.

Serves the public config API and Entra/service-key gated admin CRUD endpoints.
Config is stored in Azure Table Storage (releaseNotesConfig table).
Channel lists are proxied from the GitHub repo (populated by the cron job).
"""
from __future__ import annotations

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request

from .auth import require_authenticated
from .config_store import ConfigStore

GITHUB_RAW = "https://raw.githubusercontent.com/DavidsonCollege/release-notes-monitor/main"

app = FastAPI(title="Release Notes Monitor API")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ── Public API ────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_public_config():
    return ConfigStore().read()


@app.get("/api/channels/{kind}")
async def get_channels(kind: str):
    if kind not in ("slack", "zoom"):
        raise HTTPException(status_code=404, detail="Unknown channel kind")
    url = f"{GITHUB_RAW}/docs/{kind}_channels.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers={"User-Agent": "dc-release-notes-monitor-web"})
            if r.status_code == 200:
                return r.json()
    except Exception as exc:
        print(f"  /api/channels/{kind}: upstream error: {exc}")
    return {"channels": [], "fetch_status": "error"}


# ── Admin API (service-key or Easy Auth gated) ────────────────────────────────

@app.get("/api/admin/me")
def whoami(principal: dict = Depends(require_authenticated)):
    return {
        "name": principal.get("userDetails") or principal.get("name"),
        "provider": principal.get("identityProvider") or principal.get("auth_typ"),
    }


@app.get("/api/admin/config", dependencies=[Depends(require_authenticated)])
def get_admin_config():
    return ConfigStore().read_with_etag()


@app.put("/api/admin/config", dependencies=[Depends(require_authenticated)])
async def save_admin_config(request: Request):
    body = await request.json()
    etag = request.headers.get("if-match")
    config = body.get("config", body)
    return ConfigStore().write(config, etag=etag)
