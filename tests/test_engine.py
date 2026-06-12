from unittest.mock import AsyncMock

import pytest

from src.config import Playbook
from src.engine import run_engine
from src.superset_client import SupersetError

PLAYBOOK_RAW = {
    "checks": [
        {
            "id": "sr",
            "name": "Success Rate",
            "summary_chart_id": 412,
            "metric": "success_rate",
            "rules": [{"type": "threshold", "op": ">=", "value": 98.0}],
            "drilldown": [{"chart_id": 415, "describe": "by method"}],
            "deep_dive": "off",
            "severity": "high",
        }
    ]
}


@pytest.mark.asyncio
async def test_normal_check_no_drilldown():
    playbook = Playbook.model_validate(PLAYBOOK_RAW)
    mock_client = AsyncMock()
    mock_client.get_chart_data.return_value = {
        "result": [{"data": [{"success_rate": 99.5}]}]
    }
    results = await run_engine(playbook, mock_client)
    assert len(results) == 1
    assert results[0].is_abnormal is False
    assert results[0].check_id == "sr"
    mock_client.get_chart_data.assert_called_once_with(412)


@pytest.mark.asyncio
async def test_abnormal_check_fetches_drilldown():
    playbook = Playbook.model_validate(PLAYBOOK_RAW)
    mock_client = AsyncMock()
    mock_client.get_chart_data.side_effect = [
        {"result": [{"data": [{"success_rate": 95.2}]}]},
        {"result": [{"data": [{"method": "qr", "rate": 80.1}]}]},
    ]
    results = await run_engine(playbook, mock_client)
    assert results[0].is_abnormal is True
    assert results[0].drilldown_data is not None
    assert len(results[0].drilldown_data) == 1
    assert mock_client.get_chart_data.call_count == 2


@pytest.mark.asyncio
async def test_superset_error_isolates_check():
    playbook = Playbook.model_validate(PLAYBOOK_RAW)
    mock_client = AsyncMock()
    mock_client.get_chart_data.side_effect = SupersetError("timeout")
    results = await run_engine(playbook, mock_client)
    assert results[0].error == "timeout"
    assert results[0].is_abnormal is False


@pytest.mark.asyncio
async def test_missing_metric_marks_error():
    playbook = Playbook.model_validate(PLAYBOOK_RAW)
    mock_client = AsyncMock()
    mock_client.get_chart_data.return_value = {
        "result": [{"data": [{"other_metric": 42}]}]
    }
    results = await run_engine(playbook, mock_client)
    assert results[0].error is not None
    assert "success_rate" in results[0].error
