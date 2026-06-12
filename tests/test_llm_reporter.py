from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine import CheckResult, DrilldownResult
from src.llm_reporter import LLMReporter, ReasonResponse
from src.rule_engine import RuleResult


def _make_abnormal_result() -> CheckResult:
    cr = CheckResult(check_id="sr", name="Success Rate", severity="high")
    cr.is_abnormal = True
    cr.current_value = 95.2
    cr.rule_result = RuleResult(
        is_abnormal=True,
        triggered_rules=["threshold >= 98.0: current=95.2"],
    )
    cr.drilldown_data = [
        DrilldownResult(
            chart_id=415,
            describe="success rate by payment method",
            data={"result": [{"data": [{"method": "bank_transfer", "rate": 78.0}]}]},
        )
    ]
    return cr


@pytest.mark.asyncio
async def test_reporter_returns_reason_and_confidence():
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"reason": "Drop driven by bank_transfer failures.", '
        '"confident": true, "confidence_note": ""}'
    )
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

    reporter = LLMReporter(client=mock_openai, model="gpt-4o")
    result = await reporter.explain(check_result=_make_abnormal_result())

    assert isinstance(result, ReasonResponse)
    assert result.confident is True
    assert "bank_transfer" in result.reason


@pytest.mark.asyncio
async def test_reporter_fallback_on_llm_error():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock(side_effect=Exception("LLM down"))

    reporter = LLMReporter(client=mock_openai, model="gpt-4o")
    result = await reporter.explain(check_result=_make_abnormal_result())

    assert result.reason != ""
    assert result.confident is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_reporter_handles_malformed_json():
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "not json at all"
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

    reporter = LLMReporter(client=mock_openai, model="gpt-4o")
    result = await reporter.explain(check_result=_make_abnormal_result())

    assert result.confident is False
    assert result.error is not None
