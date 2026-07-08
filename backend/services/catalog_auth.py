"""JWT auth client for the external Qargo catalog API.

Holds the access/refresh tokens **in memory only** — never persisted to disk or
the database. A single shared instance is used by ``CatalogSyncService`` so that
concurrent store syncs reuse one token and never trigger parallel logins
(guarded by an :class:`asyncio.Lock`).

Auth flow (endpoints per the API):
    login   → POST {base}/api/auth/login/      {email, password} -> {access, refresh, user}
    refresh → POST {base}/auth/token/refresh/  {refresh}          -> {access}
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from backend.config import settings

_LOGIN_PATH = "/api/auth/login/"
_REFRESH_PATH = "/auth/token/refresh/"
_TIMEOUT = 30.0


class CatalogAuthError(RuntimeError):
    """Raised when the catalog API rejects login / refresh or is unreachable."""


class CatalogAuthClient:
    """Manages the JWT lifecycle for the catalog API. Tokens live in memory."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self._base_url = (base_url or settings.CATALOG_API_BASE_URL or "").rstrip("/")
        self._email = email or settings.CATALOG_API_EMAIL
        self._password = password or settings.CATALOG_API_PASSWORD
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._lock = asyncio.Lock()

    # ── Public API ──────────────────────────────────────────────────────────
    async def get_headers(self) -> dict:
        """Return the Authorization header, logging in on first use."""
        if not self._access_token:
            await self.login()
        return {"Authorization": f"Bearer {self._access_token}"}

    async def login(self) -> None:
        """Full login with email + password from the environment."""
        if not (self._base_url and self._email and self._password):
            raise CatalogAuthError(
                "Catalog API credentials are not configured (set "
                "CATALOG_API_BASE_URL, CATALOG_API_EMAIL, CATALOG_API_PASSWORD)."
            )
        async with self._lock:
            # Another coroutine may have logged in while we waited on the lock.
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                try:
                    r = await client.post(
                        f"{self._base_url}{_LOGIN_PATH}",
                        json={"email": self._email, "password": self._password},
                    )
                except httpx.HTTPError as exc:
                    raise CatalogAuthError(f"Login request failed: {exc}") from exc
            if r.status_code != 200:
                raise CatalogAuthError(
                    f"Login rejected ({r.status_code}): {r.text[:200]}"
                )
            data = r.json()
            self._access_token = data.get("access")
            self._refresh_token = data.get("refresh")
            if not self._access_token:
                raise CatalogAuthError("Login response missing 'access' token.")

    async def refresh(self) -> None:
        """Refresh the access token. Falls back to a full login on 401/failure."""
        if not self._refresh_token:
            await self.login()
            return
        async with self._lock:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                try:
                    r = await client.post(
                        f"{self._base_url}{_REFRESH_PATH}",
                        json={"refresh": self._refresh_token},
                    )
                except httpx.HTTPError:
                    r = None
            if r is not None and r.status_code == 200:
                access = r.json().get("access")
                if access:
                    self._access_token = access
                    return
        # Refresh failed or returned no token → full re-login.
        await self.login()


# Process-wide singleton shared by CatalogSyncService.
_singleton: Optional[CatalogAuthClient] = None


def get_catalog_auth() -> CatalogAuthClient:
    global _singleton
    if _singleton is None:
        _singleton = CatalogAuthClient()
    return _singleton
