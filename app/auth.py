"""Container Apps Easy Auth header parsing + service-key bypass."""
from __future__ import annotations
import base64, json, os, secrets
from fastapi import Header, HTTPException

_SERVICE_API_KEY: str = os.environ.get("SERVICE_API_KEY", "").strip()


def _decode_principal(encoded: str) -> dict | None:
    try:
        return json.loads(base64.b64decode(encoded).decode("utf-8"))
    except Exception:
        return None


def _service_principal() -> dict:
    return {
        "identityProvider": "aad",
        "auth_typ": "aad",
        "userDetails": "service-account@davidson.edu",
        "name": "Service Account (dc-adminapi)",
    }


def get_principal(
    x_ms_client_principal: str | None = Header(default=None),
    x_service_api_key: str | None = Header(default=None),
) -> dict | None:
    if (x_service_api_key and _SERVICE_API_KEY
            and secrets.compare_digest(x_service_api_key, _SERVICE_API_KEY)):
        return _service_principal()
    if not x_ms_client_principal:
        return None
    return _decode_principal(x_ms_client_principal)


def require_authenticated(
    x_ms_client_principal: str | None = Header(default=None),
    x_service_api_key: str | None = Header(default=None),
) -> dict:
    principal = get_principal(x_ms_client_principal, x_service_api_key)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")
    provider = (principal.get("identityProvider") or principal.get("auth_typ") or "")
    if provider not in ("aad", "AAD", "azureactivedirectory"):
        raise HTTPException(status_code=401, detail="Entra ID authentication required")
    return principal
