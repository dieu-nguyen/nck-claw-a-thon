from src.engine import CheckResult
from src.report_builder import build_report


def _normal(check_id="sr", check_name="Success Rate"):
    cr = CheckResult(check_id=check_id, check_name=check_name)
    cr.is_abnormal = False
    cr.status = "normal"
    cr.summary = "All metrics normal."
    return cr


def _abnormal(check_id="sr", check_name="Success Rate", status="critical", extra_charts=0):
    cr = CheckResult(check_id=check_id, check_name=check_name)
    cr.is_abnormal = True
    cr.status = status
    cr.summary = "SR tổng 93.2%, giảm 4.1% so với hôm qua."
    cr.analysis = "Lỗi cục bộ: Bank X kéo SR xuống."
    cr.recommendations = ["Kiểm tra log gateway", "Liên hệ bank X"]
    cr.extra_charts_fetched = extra_charts
    return cr


def test_all_clear_report():
    report = build_report(results=[_normal()], run_ts="2026-06-14T08:00:00")
    assert report["status"] == "all_clear"
    assert report["anomaly_count"] == 0
    assert len(report["anomalies"]) == 0
    assert "Success Rate" in report["checked_names"]


def test_report_with_anomaly():
    report = build_report(results=[_abnormal()], run_ts="2026-06-14T08:00:00")
    assert report["status"] == "issues_found"
    assert report["anomaly_count"] == 1
    a = report["anomalies"][0]
    assert a["check_id"] == "sr"
    assert a["status"] == "critical"
    assert "93.2%" in a["summary"]
    assert "Bank X" in a["analysis"]
    assert "Kiểm tra log gateway" in a["recommendations"]


def test_extra_charts_in_anomaly():
    report = build_report(results=[_abnormal(extra_charts=3)], run_ts="2026-06-14T08:00:00")
    assert report["anomalies"][0]["extra_charts_fetched"] == 3


def test_anomalies_sorted_critical_first():
    results = [_abnormal("vol", "Volume", status="warning"), _abnormal("sr", "SR", status="critical")]
    report = build_report(results=results, run_ts="2026-06-14T08:00:00")
    assert report["anomalies"][0]["check_id"] == "sr"


def test_error_check_surfaced():
    cr = CheckResult(check_id="sr", check_name="Success Rate")
    cr.error = "chart 412 timeout"
    report = build_report(results=[cr], run_ts="2026-06-14T08:00:00")
    assert len(report["errors"]) == 1
    assert "timeout" in report["errors"][0]["message"]


def test_mixed_normal_and_abnormal():
    results = [_normal("vol", "Volume"), _abnormal("sr", "Success Rate")]
    report = build_report(results=results, run_ts="2026-06-14T08:00:00")
    assert report["status"] == "issues_found"
    assert report["anomaly_count"] == 1
    assert report["total_checked"] == 2
