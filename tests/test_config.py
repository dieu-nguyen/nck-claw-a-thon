# tests/test_config.py
import pytest
from pydantic import ValidationError
from src.config import Playbook, Check, ThresholdRule, DeviationRule, DeepDiveConfig


def test_valid_playbook_parses():
    raw = {
        "deep_dive": {
            "enabled": "auto",
            "trigger": "low_confidence",
            "max_extra_charts": 5,
            "max_steps": 6,
            "scope": {"dashboard_ids": [12, 18]},
        },
        "checks": [
            {
                "id": "payment_success_rate",
                "name": "Payment Success Rate",
                "summary_chart_id": 412,
                "metric": "success_rate",
                "rules": [
                    {"type": "threshold", "op": ">=", "value": 98.0},
                    {"type": "deviation", "compare_to": "yesterday", "max_drop_pct": 2.0},
                ],
                "drilldown": [
                    {"chart_id": 415, "describe": "success rate by payment method"},
                ],
                "deep_dive": "auto",
                "severity": "high",
            }
        ],
    }
    p = Playbook.model_validate(raw)
    assert len(p.checks) == 1
    assert p.checks[0].id == "payment_success_rate"
    assert len(p.checks[0].rules) == 2
    assert p.deep_dive.max_extra_charts == 5


def test_missing_required_field_raises():
    with pytest.raises(ValidationError):
        Playbook.model_validate({"checks": [{"id": "x"}]})  # missing name, metric, etc.


def test_invalid_op_raises():
    with pytest.raises(ValidationError):
        ThresholdRule(type="threshold", op="??", value=98.0)


def test_invalid_compare_to_raises():
    with pytest.raises(ValidationError):
        DeviationRule(type="deviation", compare_to="last_month", max_drop_pct=5.0)


def test_check_deep_dive_off():
    raw = {
        "checks": [
            {
                "id": "txn_volume",
                "name": "Txn Volume",
                "summary_chart_id": 420,
                "metric": "total_txns",
                "rules": [{"type": "deviation", "compare_to": "last_week", "max_drop_pct": 15.0}],
                "drilldown": [],
                "deep_dive": "off",
                "severity": "medium",
            }
        ]
    }
    p = Playbook.model_validate(raw)
    assert p.checks[0].deep_dive == "off"


import os
import tempfile

import yaml

from src.playbook import PlaybookLoadError, load_playbook


def test_load_valid_playbook_from_file():
    data = {
        "checks": [
            {
                "id": "sr",
                "name": "Success Rate",
                "summary_chart_id": 1,
                "metric": "sr",
                "rules": [{"type": "threshold", "op": ">=", "value": 98.0}],
                "drilldown": [],
            }
        ]
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        path = f.name
    try:
        playbook = load_playbook(path)
        assert playbook.checks[0].id == "sr"
    finally:
        os.unlink(path)


def test_load_missing_file_raises():
    with pytest.raises(PlaybookLoadError, match="not found"):
        load_playbook("/nonexistent/path.yaml")


def test_load_invalid_yaml_raises():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("checks: [{ id: x }]")
        path = f.name
    try:
        with pytest.raises(PlaybookLoadError, match="validation"):
            load_playbook(path)
    finally:
        os.unlink(path)
