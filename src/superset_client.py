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

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

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

    def _chart_data_body(self, chart: dict) -> dict:
        query_context = chart.get("query_context")
        if isinstance(query_context, str):
            query_context = json.loads(query_context)
        if not query_context:
            raise SupersetError(f"Chart {chart.get('id')} has no query_context")

        form_data = query_context.get("form_data")
        if isinstance(form_data, str):
            form_data = json.loads(form_data)
        if not form_data:
            params = chart.get("params")
            form_data = json.loads(params) if isinstance(params, str) else params

        return {
            "datasource": query_context["datasource"],
            "queries": query_context["queries"],
            "form_data": form_data,
            "result_format": "json",
            "result_type": "full",
        }

    async def get_chart_data(self, chart_id: int) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            await self._login(client)
            chart_resp = await self._request_with_retry(
                client, "GET", f"{self._base}/api/v1/chart/{chart_id}"
            )
            chart = chart_resp.get("result", chart_resp)
            body = self._chart_data_body(chart)
            return await self._request_with_retry(
                client, "POST", f"{self._base}/api/v1/chart/data", json=body
            )

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
