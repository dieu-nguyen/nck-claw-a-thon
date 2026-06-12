from __future__ import annotations

import json
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from src.config import DeepDiveConfig
from src.engine import CheckResult
from src.superset_client import SupersetClient, SupersetError

_SYSTEM_PROMPT = """\
You are a fintech monitoring analyst running a bounded investigation to explain an
anomaly. You have access to two read-only tools:
  - list_charts(dashboard_id): list charts on a dashboard
  - get_chart_data(chart_id): fetch the data for a specific chart

At each step, respond ONLY with valid JSON in one of two schemas:
1. To use a tool:
   {"action": "list_charts"|"get_chart_data", "args": {<args>}}
2. When you have enough to explain the root cause, or want to give up:
   {"action": "done", "reason": "<explanation>", "confident": true|false}

You may ONLY access dashboards in the allowed scope provided.
Stop as soon as you can explain the root cause confidently.
"""


@dataclass
class DeepDiveResult:
    final_reason: str
    was_confident: bool
    extra_charts_examined: int = 0
    steps_taken: int = 0
    audit_log: list[str] = field(default_factory=list)
    error: str | None = None


def _summarize_check(cr: CheckResult) -> str:
    rules = cr.rule_result.triggered_rules if cr.rule_result else []
    lines = [
        f"Metric: {cr.name}, current value: {cr.current_value}",
        f"Triggered rules: {rules}",
        "Fixed drill-down data already collected:",
    ]
    if cr.drilldown_data:
        for dd in cr.drilldown_data:
            lines.append(f"  {dd.describe}: {json.dumps(dd.data, default=str)[:1000]}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


class DeepDiveInvestigator:
    def __init__(self, superset: SupersetClient, llm_client: AsyncOpenAI, model: str):
        self._superset = superset
        self._llm = llm_client
        self._model = model

    async def investigate(
        self, check_result: CheckResult, config: DeepDiveConfig
    ) -> DeepDiveResult:
        allowed = set(config.scope.dashboard_ids)
        budget_charts = config.max_extra_charts
        budget_steps = config.max_steps
        audit: list[str] = []
        extra_charts = 0
        steps = 0
        context = _summarize_check(check_result)
        messages = [
            {
                "role": "system",
                "content": _SYSTEM_PROMPT + f"\nAllowed dashboard IDs: {list(allowed)}",
            },
            {"role": "user", "content": context},
        ]

        try:
            while steps < budget_steps and extra_charts <= budget_charts:
                resp = await self._llm.chat.completions.create(
                    model=self._model, messages=messages, temperature=0
                )
                steps += 1
                raw = resp.choices[0].message.content
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    audit.append(f"step {steps}: LLM returned invalid JSON — stopping")
                    break

                action = parsed.get("action")

                if action == "done":
                    return DeepDiveResult(
                        final_reason=parsed.get("reason", ""),
                        was_confident=bool(parsed.get("confident", False)),
                        extra_charts_examined=extra_charts,
                        steps_taken=steps,
                        audit_log=audit,
                    )

                if action == "list_charts":
                    dashboard_id = parsed["args"]["dashboard_id"]
                    if dashboard_id not in allowed:
                        audit.append(
                            f"step {steps}: list_charts(dashboard_id={dashboard_id}) "
                            "refused — out of scope"
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": f"Error: dashboard {dashboard_id} is out of scope.",
                            }
                        )
                        continue
                    try:
                        charts = await self._superset.list_charts(dashboard_id)
                        audit.append(
                            f"step {steps}: list_charts(dashboard_id={dashboard_id}) "
                            f"→ {len(charts)} charts"
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    f"Charts on dashboard {dashboard_id}: "
                                    f"{json.dumps(charts, default=str)[:1500]}"
                                ),
                            }
                        )
                    except SupersetError as e:
                        audit.append(f"step {steps}: list_charts error: {e}")
                        messages.append(
                            {"role": "user", "content": f"Error fetching charts: {e}"}
                        )

                elif action == "get_chart_data":
                    if extra_charts >= budget_charts:
                        audit.append(
                            f"step {steps}: budget exhausted ({budget_charts} extra charts)"
                        )
                        break
                    chart_id = parsed["args"]["chart_id"]
                    try:
                        data = await self._superset.get_chart_data(chart_id)
                        extra_charts += 1
                        audit.append(f"step {steps}: get_chart_data(chart_id={chart_id}) → OK")
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    f"Chart {chart_id} data: "
                                    f"{json.dumps(data, default=str)[:1500]}"
                                ),
                            }
                        )
                    except SupersetError as e:
                        audit.append(f"step {steps}: get_chart_data({chart_id}) error: {e}")
                        messages.append(
                            {
                                "role": "user",
                                "content": f"Error fetching chart {chart_id}: {e}",
                            }
                        )

            audit.append("deep-dive loop ended (budget or step limit reached)")
            messages.append(
                {
                    "role": "user",
                    "content": (
                        'Budget reached. Provide your best explanation now using the data '
                        'collected. Respond with {"action": "done", "reason": "...", '
                        '"confident": false}.'
                    ),
                }
            )
            resp = await self._llm.chat.completions.create(
                model=self._model, messages=messages, temperature=0
            )
            parsed = json.loads(resp.choices[0].message.content)
            return DeepDiveResult(
                final_reason=parsed.get(
                    "reason", "Could not determine root cause within budget."
                ),
                was_confident=False,
                extra_charts_examined=extra_charts,
                steps_taken=steps,
                audit_log=audit,
            )

        except Exception as e:
            audit.append(f"deep-dive error: {e}")
            return DeepDiveResult(
                final_reason="Deep-dive failed; raw findings available above.",
                was_confident=False,
                extra_charts_examined=extra_charts,
                steps_taken=steps,
                audit_log=audit,
                error=str(e),
            )
