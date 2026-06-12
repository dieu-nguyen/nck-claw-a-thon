from __future__ import annotations

from src.engine import CheckResult
from src.llm_reporter import ReasonResponse

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def build_report(
    results: list[CheckResult],
    reasons: dict[str, ReasonResponse],
    run_ts: str,
) -> dict:
    anomalies = []
    errors = []
    checked_names = []

    for cr in results:
        checked_names.append(cr.name)
        if cr.error:
            errors.append({"check_id": cr.check_id, "name": cr.name, "message": cr.error})
            continue
        if not cr.is_abnormal:
            continue
        reason_resp = reasons.get(cr.check_id)
        anomalies.append(
            {
                "check_id": cr.check_id,
                "name": cr.name,
                "severity": cr.severity,
                "current_value": cr.current_value,
                "triggered_rules": cr.rule_result.triggered_rules if cr.rule_result else [],
                "skipped_rules": cr.rule_result.skipped_rules if cr.rule_result else [],
                "reason": reason_resp.reason if reason_resp else "",
                "deep_dive_tag": (
                    f"deep-dive: examined {cr.deep_dive_charts_examined} extra charts"
                    if cr.deep_dive_used
                    else None
                ),
            }
        )

    anomalies.sort(key=lambda a: _SEVERITY_ORDER.get(a["severity"], 99))

    return {
        "status": "all_clear" if not anomalies else "issues_found",
        "run_ts": run_ts,
        "anomaly_count": len(anomalies),
        "total_checked": len(results),
        "checked_names": checked_names,
        "anomalies": anomalies,
        "errors": errors,
    }
