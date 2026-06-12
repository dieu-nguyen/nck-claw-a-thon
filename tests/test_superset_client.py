import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from src.superset_client import SupersetClient, SupersetError

BASE = "https://superset.example.com"

CHART_META = {
    "result": {
        "id": 412,
        "query_context": json.dumps(
            {
                "datasource": {"id": 1, "type": "table"},
                "queries": [{"row_limit": 1000}],
                "form_data": {},
            }
        ),
    }
}


@pytest.fixture
def client():
    return SupersetClient(base_url=BASE, username="user", password="pass")


def _mock_login(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/api/v1/security/login",
        json={"access_token": "tok123"},
    )


@pytest.mark.asyncio
async def test_get_chart_data_success(client, httpx_mock: HTTPXMock):
    _mock_login(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/api/v1/chart/412",
        json=CHART_META,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/api/v1/chart/data",
        json={"result": [{"data": [{"success_rate": 99.1, "prev_success_rate": 98.8}]}]},
    )
    result = await client.get_chart_data(412)
    assert result["result"][0]["data"][0]["success_rate"] == 99.1


@pytest.mark.asyncio
async def test_get_chart_data_retries_on_timeout(client, httpx_mock: HTTPXMock):
    _mock_login(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/api/v1/chart/412",
        json=CHART_META,
    )
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"))
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/api/v1/chart/data",
        json={"result": [{"data": []}]},
    )
    result = await client.get_chart_data(412)
    assert result["result"] == [{"data": []}]


@pytest.mark.asyncio
async def test_get_chart_data_raises_after_max_retries(client, httpx_mock: HTTPXMock):
    _mock_login(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/api/v1/chart/412",
        json=CHART_META,
    )
    for _ in range(3):
        httpx_mock.add_exception(httpx.ReadTimeout("timeout"))
    with pytest.raises(SupersetError, match="timeout"):
        await client.get_chart_data(412)


@pytest.mark.asyncio
async def test_list_charts_success(client, httpx_mock: HTTPXMock):
    _mock_login(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/api/v1/dashboard/12/charts",
        json={"result": [{"id": 415, "slice_name": "Success by Method"}]},
    )
    charts = await client.list_charts(dashboard_id=12)
    assert charts[0]["id"] == 415
