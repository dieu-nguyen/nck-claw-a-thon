from src.engine import CheckResult
from src.llm_reporter import ReasonResponse
from src.report_builder import build_report
from src.rule_engine import RuleResult


def _normal(check_id="sr", name="Success Rate"):
    cr = CheckResult(check_id=check_id, name=name, severity="high")
    cr.is_abnormal = False
    cr.current_value = 99.1
    cr.rule_result = RuleResult()
    return cr


def _abnormal(check_id="sr", name="Success Rate", deep_dive_charts=0):
    cr = CheckResult(check_id=check_id, name=name, severity="high")
    cr.is_abnormal = True
    cr.current_value = 95.2
    cr.rule_result = RuleResult(
        is_abnormal=True,
        triggered_rules=["threshold >= 98.0: current=95.2"],
    )
    cr.deep_dive_used = deep_dive_charts > 0
    cr.deep_dive_charts_examined = deep_dive_charts
    return cr


def _reason(text="Bank X caused it.", confident=True):
    return ReasonResponse(reason=text, confident=confident)


def test_all_clear_report():
    report = build_report(
        results=[_normal()],
        reasons={},
        run_ts="2026-06-11T08:00:00",
    )
    assert report["status"] == "all_clear"
    assert report["anomaly_count"] == 0
    assert len(report["anomalies"]) == 0
    assert "Success Rate" in report["checked_names"]


def test_report_with_anomaly():
    cr = _abnormal()
    report = build_report(
        results=[cr],
        reasons={"sr": _reason()},
        run_ts="2026-06-11T08:00:00",
    )
    assert report["status"] == "issues_found"
    assert report["anomaly_count"] == 1
    a = report["anomalies"][0]
    assert a["check_id"] == "sr"
    assert a["current_value"] == 95.2
    assert a["reason"] == "Bank X caused it."
    assert a["deep_dive_tag"] is None


def test_deep_dive_tag_present():
    cr = _abnormal(deep_dive_charts=3)
    report = build_report(
        results=[cr],
        reasons={"sr": _reason()},
        run_ts="2026-06-11T08:00:00",
    )
    assert report["anomalies"][0]["deep_dive_tag"] == "deep-dive: examined 3 extra charts"


def test_anomalies_sorted_by_severity():
    results = [
        _abnormal("v", "Volume", 0),
        _abnormal("sr", "Success Rate", 0),
    ]
    results[0].severity = "medium"
    results[1].severity = "high"
    reasons = {"v": _reason("vol drop"), "sr": _reason("sr drop")}
    report = build_report(results=results, reasons=reasons, run_ts="2026-06-11T08:00:00")
    assert report["anomalies"][0]["check_id"] == "sr"


def test_error_check_surfaced():
    cr = CheckResult(check_id="sr", name="Success Rate", severity="high")
    cr.error = "chart 412 timeout"
    report = build_report(results=[cr], reasons={}, run_ts="2026-06-11T08:00:00")
    assert len(report["errors"]) == 1
    assert "timeout" in report["errors"][0]["message"]
