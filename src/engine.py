from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.config import Check, Playbook
from src.rule_engine import RuleResult, evaluate_rules
from src.superset_client import SupersetClient, SupersetError


@dataclass
class DrilldownResult:
    chart_id: int
    describe: str
    data: Any


@dataclass
class CheckResult:
    check_id: str
    name: str
    severity: str
    is_abnormal: bool = False
    current_value: float | None = None
    rule_result: RuleResult | None = None
    drilldown_data: list[DrilldownResult] | None = None
    error: str | None = None
    deep_dive_used: bool = False
    deep_dive_charts_examined: int = 0


def _extract_metric(data: dict, metric: str) -> tuple[float, dict]:
    rows: list[dict] = []
    for result_item in data.get("result", []):
        rows.extend(result_item.get("data", []))
    if not rows:
        raise KeyError(f"No rows in chart data for metric '{metric}'")
    row = rows[0]
    if metric not in row:
        raise KeyError(
            f"Metric '{metric}' not found in chart data. Available: {list(row.keys())}"
        )
    return float(row[metric]), row


async def run_engine(playbook: Playbook, client: SupersetClient) -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in playbook.checks:
        result = await _run_check(check, client)
        results.append(result)
    return results


async def _run_check(check: Check, client: SupersetClient) -> CheckResult:
    cr = CheckResult(check_id=check.id, name=check.name, severity=check.severity)
    try:
        chart_data = await client.get_chart_data(check.summary_chart_id)
        current, row = _extract_metric(chart_data, check.metric)
        cr.current_value = current
        rule_result = evaluate_rules(check.rules, current=current, chart_data=row)
        cr.rule_result = rule_result
        cr.is_abnormal = rule_result.is_abnormal

        if cr.is_abnormal and check.drilldown:
            drilldown_results = []
            for dd in check.drilldown:
                dd_data = await client.get_chart_data(dd.chart_id)
                drilldown_results.append(
                    DrilldownResult(chart_id=dd.chart_id, describe=dd.describe, data=dd_data)
                )
            cr.drilldown_data = drilldown_results

    except SupersetError as e:
        cr.error = str(e)
    except KeyError as e:
        cr.error = str(e)
    except Exception as e:
        cr.error = f"Unexpected error: {e}"

    return cr
