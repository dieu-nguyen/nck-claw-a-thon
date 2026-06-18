import asyncio
import json
from typing import Any

import httpx


class SupersetError(Exception):
    pass


class SupersetClient:
    def __init__(self, base_url: str, username: str, password: str, retries: int = 2):
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._retries = retries
        self._token: str | None = None
        self._csrf: str = ""

    async def _login(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            f"{self._base}/api/v1/security/login",
            json={
                "username": self._username,
                "password": self._password,
                "provider": "db",
                "refresh": True,
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        # Fetch CSRF token — required for POST endpoints like /api/v1/chart/data
        csrf_resp = await client.get(
            f"{self._base}/api/v1/security/csrf_token/",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        csrf_resp.raise_for_status()
        self._csrf = csrf_resp.json().get("result", "")

    def _auth_headers(self) -> dict:
        headers = {"Authorization": f"Bearer {self._token}"}
        if self._csrf:
            headers["X-CSRFToken"] = self._csrf
            headers["Referer"] = self._base
        return headers

    async def _request_with_retry(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = await client.request(
                    method, url, headers=self._auth_headers(), **kwargs
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                raise SupersetError(
                    f"Client error '{e.response.status_code} {e.response.reason_phrase}' "
                    f"for url '{e.request.url}'"
                ) from e
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                if attempt < self._retries:
                    await asyncio.sleep(1.5**attempt)
        raise SupersetError(f"Superset request failed after retries: {last_exc}") from last_exc

    async def _request_with_retry_new_session(
        self, method: str, url: str, **kwargs
    ) -> Any:
        async with httpx.AsyncClient(timeout=30) as client:
            await self._login(client)
            return await self._request_with_retry(client, method, url, **kwargs)

    async def _get_session_headers(self, client: httpx.AsyncClient) -> dict:
        """Login with web session + JWT to get headers accepted by POST endpoints."""
        import re as _re
        await client.get(f"{self._base}/login/")
        login_page = await client.get(f"{self._base}/login/")
        m = _re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login_page.text)
        web_csrf = m.group(1) if m else ""
        await client.post(f"{self._base}/login/", data={
            "username": self._username, "password": self._password, "csrf_token": web_csrf,
        })
        resp = await client.post(f"{self._base}/api/v1/security/login", json={
            "username": self._username, "password": self._password,
            "provider": "db", "refresh": True,
        })
        resp.raise_for_status()
        token = resp.json()["access_token"]
        csrf_r = await client.get(f"{self._base}/api/v1/security/csrf_token/",
                                  headers={"Authorization": f"Bearer {token}"})
        csrf_r.raise_for_status()
        csrf = csrf_r.json().get("result", "")
        return {"Authorization": f"Bearer {token}", "X-CSRFToken": csrf, "Referer": self._base}

    async def get_chart_data(self, chart_id: int) -> dict:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            headers = await self._get_session_headers(client)

            # Get the stored query_context from the chart API — it has the correct
            # datasource, columns, metrics, and filters already configured.
            chart_r = await client.get(
                f"{self._base}/api/v1/chart/{chart_id}", headers=headers)
            chart_r.raise_for_status()
            qc_str = chart_r.json().get("result", {}).get("query_context", "")
            if not qc_str:
                raise SupersetError(
                    f"Chart {chart_id}: no query_context available — open the chart in "
                    "Superset and save it once to generate query_context"
                )
            query_context = json.loads(qc_str)

            resp = await client.post(
                f"{self._base}/api/v1/chart/data",
                headers={**headers, "Content-Type": "application/json"},
                json=query_context,
            )
            resp.raise_for_status()
            return resp.json()

    async def list_charts(self, dashboard_id: int) -> list[dict]:
        result = await self._request_with_retry_new_session(
            "GET",
            f"{self._base}/api/v1/dashboard/{dashboard_id}/charts",
        )
        return result.get("result", [])

    async def search_charts(self, name: str, page_size: int = 100) -> list[dict]:
        """Return charts whose name contains `name` (case-insensitive substring match)."""
        result = await self._request_with_retry_new_session(
            "GET", f"{self._base}/api/v1/chart/?page_size={page_size}"
        )
        needle = name.lower()
        return [
            {"id": r["id"], "name": r.get("slice_name", ""), "description": r.get("description", "")}
            for r in result.get("result", [])
            if needle in r.get("slice_name", "").lower()
        ]

    async def search_dashboards(self, name: str, page_size: int = 100) -> list[dict]:
        """Return dashboards whose title contains `name` (case-insensitive substring match)."""
        result = await self._request_with_retry_new_session(
            "GET", f"{self._base}/api/v1/dashboard/?page_size={page_size}"
        )
        needle = name.lower()
        return [
            {"id": r["id"], "name": r.get("dashboard_title", ""), "url": r.get("url", "")}
            for r in result.get("result", [])
            if needle in r.get("dashboard_title", "").lower()
        ]
