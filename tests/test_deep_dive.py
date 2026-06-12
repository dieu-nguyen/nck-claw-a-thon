import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import DeepDiveConfig, DeepDiveScope
from src.deep_dive import DeepDiveInvestigator
from src.engine import CheckResult
from src.rule_engine import RuleResult
from src.superset_client import SupersetError


def _make_config(dashboard_ids=None, max_extra_charts=3, max_steps=4):
    return DeepDiveConfig(
        enabled="auto",
        trigger="low_confidence",
        max_extra_charts=max_extra_charts,
        max_steps=max_steps,
        scope=DeepDiveScope(dashboard_ids=dashboard_ids or [12]),
    )


def _make_check_result():
    cr = CheckResult(check_id="sr", name="Success Rate", severity="high")
    cr.is_abnormal = True
    cr.current_value = 95.2
    cr.rule_result = RuleResult(is_abnormal=True, triggered_rules=["threshold >= 98.0"])
    cr.drilldown_data = []
    return cr


def _tool_response(tool_name: str, args: dict):
    msg = MagicMock()
    msg.choices[0].message.content = json.dumps({"action": tool_name, "args": args})
    return msg


def _confident_response(reason: str):
    msg = MagicMock()
    msg.choices[0].message.content = json.dumps(
        {"action": "done", "reason": reason, "confident": True}
    )
    return msg


@pytest.mark.asyncio
async def test_deep_dive_exits_when_confident_after_one_step():
    mock_superset = AsyncMock()
    mock_superset.get_chart_data.return_value = {
        "result": [{"data": [{"bank": "X", "rate": 70}]}]
    }

    mock_llm = MagicMock()
    mock_llm.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_response("get_chart_data", {"chart_id": 430}),
            _confident_response("Bank X caused the drop."),
        ]
    )

    investigator = DeepDiveInvestigator(
        superset=mock_superset, llm_client=mock_llm, model="gpt-4o"
    )
    result = await investigator.investigate(
        check_result=_make_check_result(), config=_make_config()
    )
    assert result.final_reason != ""
    assert result.extra_charts_examined == 1
    assert result.was_confident is True


@pytest.mark.asyncio
async def test_deep_dive_respects_max_extra_charts_budget():
    mock_superset = AsyncMock()
    mock_superset.get_chart_data.return_value = {"result": [{"data": []}]}

    calls = [_tool_response("get_chart_data", {"chart_id": 430 + i}) for i in range(10)]
    calls.append(
        _confident_response("Budget reached explanation.")
    )
    mock_llm = MagicMock()
    mock_llm.chat.completions.create = AsyncMock(side_effect=calls)

    investigator = DeepDiveInvestigator(
        superset=mock_superset, llm_client=mock_llm, model="gpt-4o"
    )
    result = await investigator.investigate(
        check_result=_make_check_result(),
        config=_make_config(max_extra_charts=2, max_steps=10),
    )
    assert result.extra_charts_examined <= 2


@pytest.mark.asyncio
async def test_deep_dive_refuses_out_of_scope_dashboard():
    mock_superset = AsyncMock()

    mock_llm = MagicMock()
    mock_llm.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_response("list_charts", {"dashboard_id": 99}),
            _confident_response("Could not determine cause."),
        ]
    )

    investigator = DeepDiveInvestigator(
        superset=mock_superset, llm_client=mock_llm, model="gpt-4o"
    )
    result = await investigator.investigate(
        check_result=_make_check_result(), config=_make_config(dashboard_ids=[12])
    )
    mock_superset.list_charts.assert_not_called()
    assert "out of scope" in result.audit_log[-1].lower() or result.extra_charts_examined == 0


@pytest.mark.asyncio
async def test_deep_dive_fallback_on_superset_error():
    mock_superset = AsyncMock()
    mock_superset.get_chart_data.side_effect = SupersetError("timeout")

    mock_llm = MagicMock()
    mock_llm.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_response("get_chart_data", {"chart_id": 430}),
            _confident_response("Best effort."),
        ]
    )

    investigator = DeepDiveInvestigator(
        superset=mock_superset, llm_client=mock_llm, model="gpt-4o"
    )
    result = await investigator.investigate(
        check_result=_make_check_result(), config=_make_config()
    )
    assert result.final_reason != ""
