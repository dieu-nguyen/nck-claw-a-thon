from __future__ import annotations

import json
from dataclasses import dataclass

from openai import AsyncOpenAI

from src.engine import CheckResult

_SYSTEM_PROMPT = """\
You are a fintech monitoring analyst. You are given an abnormal metric finding
and the data from drill-down charts. Your job is to:
1. Write a concise plain-language explanation (2-4 sentences) of WHY the metric
   is abnormal, citing specific numbers and segments from the drill-down data.
2. Assess whether you can confidently explain the root cause from the data given.

Respond ONLY with valid JSON in this exact schema:
{
  "reason": "<plain language explanation>",
  "confident": true|false,
  "confidence_note": "<brief note if not confident, else empty string>"
}
"""


@dataclass
class ReasonResponse:
    reason: str
    confident: bool
    confidence_note: str = ""
    error: str | None = None


def _build_user_message(cr: CheckResult) -> str:
    lines = [
        f"Metric: {cr.name}",
        f"Current value: {cr.current_value}",
        f"Triggered rules: {cr.rule_result.triggered_rules if cr.rule_result else []}",
        "",
        "Drill-down data:",
    ]
    if cr.drilldown_data:
        for dd in cr.drilldown_data:
            lines.append(f"  Chart: {dd.describe}")
            lines.append(f"  Data: {json.dumps(dd.data, default=str)[:2000]}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def _fallback_reason(cr: CheckResult) -> ReasonResponse:
    rules = cr.rule_result.triggered_rules if cr.rule_result else []
    return ReasonResponse(
        reason=(
            f"{cr.name} is abnormal. Triggered: {'; '.join(rules)}. "
            f"Current value: {cr.current_value}."
        ),
        confident=False,
        error="LLM unavailable — raw findings reported",
    )


class LLMReporter:
    def __init__(self, client: AsyncOpenAI, model: str):
        self._client = client
        self._model = model

    async def explain(self, check_result: CheckResult) -> ReasonResponse:
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_message(check_result)},
                ],
                temperature=0,
            )
            raw = resp.choices[0].message.content
            parsed = json.loads(raw)
            return ReasonResponse(
                reason=parsed["reason"],
                confident=bool(parsed["confident"]),
                confidence_note=parsed.get("confidence_note", ""),
            )
        except json.JSONDecodeError as e:
            return ReasonResponse(
                reason="",
                confident=False,
                error=f"LLM returned invalid JSON: {e}",
            )
        except Exception:
            return _fallback_reason(check_result)
