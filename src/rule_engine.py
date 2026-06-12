from dataclasses import dataclass, field

from src.config import DeviationRule, Rule, ThresholdRule

BASELINE_KEYS = {
    "yesterday": "previous_value",
    "last_week": "last_week_value",
    "7d_avg": "seven_day_avg",
}

OPS = {
    ">=": lambda a, b: a < b,
    ">": lambda a, b: a <= b,
    "<=": lambda a, b: a > b,
    "<": lambda a, b: a >= b,
    "==": lambda a, b: a != b,
}


@dataclass
class RuleResult:
    is_abnormal: bool = False
    triggered_rules: list[str] = field(default_factory=list)
    skipped_rules: list[str] = field(default_factory=list)


def evaluate_rules(rules: list[Rule], current: float, chart_data: dict) -> RuleResult:
    result = RuleResult()
    for rule in rules:
        if isinstance(rule, ThresholdRule):
            breach_fn = OPS[rule.op]
            if breach_fn(current, rule.value):
                result.is_abnormal = True
                result.triggered_rules.append(
                    f"threshold {rule.op} {rule.value}: current={current}"
                )
        elif isinstance(rule, DeviationRule):
            key = BASELINE_KEYS[rule.compare_to]
            baseline = chart_data.get(key)
            if baseline is None:
                result.skipped_rules.append(
                    f"deviation:{rule.compare_to} (no baseline in chart data)"
                )
                continue
            if baseline == 0:
                continue
            drop_pct = (baseline - current) / baseline * 100
            if drop_pct > rule.max_drop_pct:
                result.is_abnormal = True
                result.triggered_rules.append(
                    f"deviation vs {rule.compare_to}: dropped {drop_pct:.2f}% "
                    f"(max allowed {rule.max_drop_pct}%)"
                )
    return result
