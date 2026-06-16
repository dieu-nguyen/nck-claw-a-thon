from __future__ import annotations

from src.engine import CheckResult

_STATUS_ORDER = {"critical": 0, "warning": 1, "normal": 2}


def build_report(results: list[CheckResult], run_ts: str) -> dict:
    anomalies = []
    normal = []
    errors = []
    checked_names = []

    for cr in results:
        checked_names.append(cr.check_name)
        if cr.error:
            errors.append({"check_id": cr.check_id, "name": cr.check_name, "message": cr.error})
            continue
        if not cr.is_abnormal:
            normal.append({
                "check_id": cr.check_id,
                "name": cr.check_name,
                "summary": cr.summary,
            })
            continue
        anomalies.append({
            "check_id": cr.check_id,
            "name": cr.check_name,
            "status": cr.status,
            "summary": cr.summary,
            "analysis": cr.analysis,
            "recommendations": cr.recommendations,
            "extra_charts_fetched": cr.extra_charts_fetched,
        })

    anomalies.sort(key=lambda a: _STATUS_ORDER.get(a["status"], 99))

    return {
        "status": "all_clear" if not anomalies else "issues_found",
        "run_ts": run_ts,
        "anomaly_count": len(anomalies),
        "total_checked": len(results),
        "checked_names": checked_names,
        "anomalies": anomalies,
        "normal": normal,
        "errors": errors,
    }
