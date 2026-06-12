from src.config import DeviationRule, ThresholdRule
from src.rule_engine import evaluate_rules


def test_threshold_pass():
    rule = ThresholdRule(type="threshold", op=">=", value=98.0)
    result = evaluate_rules([rule], current=99.1, chart_data={})
    assert result.is_abnormal is False
    assert result.triggered_rules == []


def test_threshold_breach():
    rule = ThresholdRule(type="threshold", op=">=", value=98.0)
    result = evaluate_rules([rule], current=95.2, chart_data={})
    assert result.is_abnormal is True
    assert len(result.triggered_rules) == 1
    assert "95.2" in result.triggered_rules[0]


def test_deviation_breach_yesterday():
    rule = DeviationRule(type="deviation", compare_to="yesterday", max_drop_pct=2.0)
    chart_data = {"previous_value": 98.5}
    result = evaluate_rules([rule], current=95.2, chart_data=chart_data)
    assert result.is_abnormal is True
    drop_str = result.triggered_rules[0]
    assert "3.35" in drop_str or "3.3" in drop_str


def test_deviation_pass():
    rule = DeviationRule(type="deviation", compare_to="yesterday", max_drop_pct=5.0)
    chart_data = {"previous_value": 99.0}
    result = evaluate_rules([rule], current=98.8, chart_data=chart_data)
    assert result.is_abnormal is False


def test_deviation_skipped_when_no_baseline():
    rule = DeviationRule(type="deviation", compare_to="yesterday", max_drop_pct=2.0)
    result = evaluate_rules([rule], current=99.0, chart_data={})
    assert result.is_abnormal is False
    assert result.skipped_rules == ["deviation:yesterday (no baseline in chart data)"]


def test_multiple_rules_any_triggers():
    rules = [
        ThresholdRule(type="threshold", op=">=", value=98.0),
        DeviationRule(type="deviation", compare_to="yesterday", max_drop_pct=2.0),
    ]
    chart_data = {"previous_value": 99.0}
    result = evaluate_rules(rules, current=96.0, chart_data=chart_data)
    assert result.is_abnormal is True


def test_all_ops():
    for op, val, current, should_breach in [
        (">=", 98.0, 97.9, True),
        (">=", 98.0, 98.0, False),
        (">", 98.0, 98.0, True),
        ("<=", 5.0, 5.1, True),
        ("<", 5.0, 5.0, True),
        ("==", 5.0, 5.0, False),
        ("==", 5.0, 4.9, True),
    ]:
        rule = ThresholdRule(type="threshold", op=op, value=val)
        result = evaluate_rules([rule], current=current, chart_data={})
        assert result.is_abnormal is should_breach, f"op={op} val={val} current={current}"
