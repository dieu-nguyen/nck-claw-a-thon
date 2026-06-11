# Dashboard Monitoring Assistant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fintech monitoring agent that scans Superset dashboards daily, detects metric anomalies via a declarative playbook, drills into detail charts, optionally escalates to a bounded LLM deep-dive, and returns a structured JSON report consumed by a Teams Workflow Adaptive Card.

**Architecture:** Deterministic monitoring engine (playbook → fetch → rule eval → fixed drill-down) with a bounded agentic LLM escalation when the fixed drill-down doesn't explain the anomaly. Single FastAPI `/run` endpoint on GreenNode AgentBase; Teams Workflows handle scheduling and rendering.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, PyYAML, httpx (async), openai SDK (AgentBase LLM), pytest + pytest-asyncio, Docker.

---

## File Map

```
nck-claw-a-thon/
├── src/
│   ├── config.py          # Pydantic models for the playbook schema
│   ├── playbook.py        # YAML loader + validator (uses config.py)
│   ├── superset_client.py # Superset REST API: get_chart_data, list_charts
│   ├── rule_engine.py     # threshold + deviation rule evaluation (pure)
│   ├── engine.py          # deterministic monitoring loop (uses all of above)
│   ├── llm_reporter.py    # LLM reason + confidence self-assessment
│   ├── deep_dive.py       # bounded deep-dive investigator loop
│   ├── report_builder.py  # assemble CheckResult list → report JSON
│   ├── run_log.py         # structured per-run audit log writer
│   └── main.py            # FastAPI app, /run endpoint + --dry-run CLI
├── tests/
│   ├── conftest.py        # shared fixtures
│   ├── test_config.py     # playbook schema validation
│   ├── test_rule_engine.py
│   ├── test_engine.py
│   ├── test_superset_client.py
│   ├── test_deep_dive.py
│   ├── test_llm_reporter.py
│   └── test_report_builder.py
├── playbook.yaml          # example/starter playbook
├── Dockerfile
├── requirements.txt
└── .env.example
```

Each file has one clear responsibility. The engine depends on `config`, `superset_client`, and `rule_engine`. The deep-dive depends on `superset_client` and `llm_reporter`. `main.py` wires them together.

---

## Task 1: Project scaffold

**Files:**
- Create: `requirements.txt`
- Create: `Dockerfile`
- Create: `.env.example`
- Create: `src/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
pydantic==2.7.1
pyyaml==6.0.1
httpx==0.27.0
openai==1.30.1
pytest==8.2.0
pytest-asyncio==0.23.7
pytest-httpx==0.30.0
python-dotenv==1.0.1
```

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY playbook.yaml .
ENV PYTHONPATH=/app
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 3: Create `.env.example`**

```
# Superset
SUPERSET_BASE_URL=https://your-superset.example.com
SUPERSET_USERNAME=your_username
SUPERSET_PASSWORD=your_password

# AgentBase LLM (OpenAI-compatible)
LLM_BASE_URL=https://llm.agentbase.example.com/v1
LLM_API_KEY=your_llm_key
LLM_MODEL=gpt-4o

# Agent
PLAYBOOK_PATH=playbook.yaml
RUN_LOG_DIR=./logs
API_TOKEN=your_secret_token_for_the_run_endpoint
```

- [ ] **Step 4: Create empty init files**

```bash
mkdir -p src tests logs
touch src/__init__.py tests/__init__.py
```

- [ ] **Step 5: Create `tests/conftest.py`**

```python
import pytest


@pytest.fixture
def sample_chart_data_normal():
    return {
        "result": [
            {
                "data": [{"metric": "success_rate", "value": 99.1, "previous_value": 98.8}]
            }
        ]
    }


@pytest.fixture
def sample_chart_data_threshold_breach():
    return {
        "result": [
            {
                "data": [{"metric": "success_rate", "value": 95.2, "previous_value": 98.5}]
            }
        ]
    }


@pytest.fixture
def sample_chart_data_no_history():
    return {
        "result": [
            {
                "data": [{"metric": "success_rate", "value": 99.1}]
            }
        ]
    }
```

- [ ] **Step 6: Verify structure**

```bash
ls src/ tests/ && cat requirements.txt
```

Expected: files present, requirements printed.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt Dockerfile .env.example src/ tests/
git commit -m "chore: project scaffold"
```

---

## Task 2: Playbook config models (`src/config.py`)

**Files:**
- Create: `src/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.config'`

- [ ] **Step 3: Implement `src/config.py`**

```python
from __future__ import annotations
from typing import Literal, Union, Annotated
from pydantic import BaseModel, Field, field_validator


class ThresholdRule(BaseModel):
    type: Literal["threshold"]
    op: Literal[">=", ">", "<=", "<", "=="]
    value: float


class DeviationRule(BaseModel):
    type: Literal["deviation"]
    compare_to: Literal["yesterday", "last_week", "7d_avg"]
    max_drop_pct: float


Rule = Annotated[Union[ThresholdRule, DeviationRule], Field(discriminator="type")]


class DrilldownChart(BaseModel):
    chart_id: int
    describe: str


class DeepDiveScope(BaseModel):
    dashboard_ids: list[int] = Field(default_factory=list)


class DeepDiveConfig(BaseModel):
    enabled: Literal["auto", "off"] = "auto"
    trigger: Literal["low_confidence", "high_severity", "always"] = "low_confidence"
    max_extra_charts: int = 5
    max_steps: int = 6
    scope: DeepDiveScope = Field(default_factory=DeepDiveScope)


class Check(BaseModel):
    id: str
    name: str
    summary_chart_id: int
    metric: str
    rules: list[Rule] = Field(min_length=1)
    drilldown: list[DrilldownChart] = Field(default_factory=list)
    deep_dive: Literal["auto", "off"] = "auto"
    severity: Literal["high", "medium", "low"] = "medium"


class Playbook(BaseModel):
    deep_dive: DeepDiveConfig = Field(default_factory=DeepDiveConfig)
    checks: list[Check] = Field(min_length=1)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_config.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: playbook config models (Pydantic)"
```

---

## Task 3: Playbook loader (`src/playbook.py`)

**Files:**
- Create: `src/playbook.py`
- Create: `playbook.yaml`
- Modify: `tests/test_config.py` (add loader tests)

- [ ] **Step 1: Write failing tests (append to `tests/test_config.py`)**

```python
import tempfile, os, yaml
from src.playbook import load_playbook


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
    from src.playbook import PlaybookLoadError
    with pytest.raises(PlaybookLoadError, match="not found"):
        load_playbook("/nonexistent/path.yaml")


def test_load_invalid_yaml_raises():
    from src.playbook import PlaybookLoadError
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("checks: [{ id: x }]")  # missing required fields
        path = f.name
    try:
        with pytest.raises(PlaybookLoadError, match="validation"):
            load_playbook(path)
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_config.py -v -k "load"
```

Expected: `ModuleNotFoundError: No module named 'src.playbook'`

- [ ] **Step 3: Implement `src/playbook.py`**

```python
import yaml
from pathlib import Path
from pydantic import ValidationError
from src.config import Playbook


class PlaybookLoadError(Exception):
    pass


def load_playbook(path: str) -> Playbook:
    p = Path(path)
    if not p.exists():
        raise PlaybookLoadError(f"Playbook not found: {path}")
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise PlaybookLoadError(f"YAML parse error: {e}") from e
    try:
        return Playbook.model_validate(raw or {})
    except ValidationError as e:
        raise PlaybookLoadError(f"Playbook validation failed:\n{e}") from e
```

- [ ] **Step 4: Create starter `playbook.yaml`**

```yaml
# Edit this file to configure your monitoring checks.
# chart_id values below are examples — replace with your real Superset chart IDs.

deep_dive:
  enabled: auto
  trigger: low_confidence
  max_extra_charts: 5
  max_steps: 6
  scope:
    dashboard_ids: []   # add your dashboard IDs here

checks:
  - id: payment_success_rate
    name: "Payment Success Rate"
    summary_chart_id: 412
    metric: success_rate
    rules:
      - type: threshold
        op: ">="
        value: 98.0
      - type: deviation
        compare_to: yesterday
        max_drop_pct: 2.0
    drilldown:
      - chart_id: 415
        describe: "success rate broken down by payment method"
      - chart_id: 417
        describe: "success rate by bank / issuer"
      - chart_id: 419
        describe: "top failure reason codes"
    deep_dive: auto
    severity: high

  - id: txn_volume
    name: "Transaction Volume"
    summary_chart_id: 420
    metric: total_txns
    rules:
      - type: deviation
        compare_to: last_week
        max_drop_pct: 15.0
    drilldown:
      - chart_id: 421
        describe: "volume by channel and merchant"
    deep_dive: off
    severity: medium
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/test_config.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/playbook.py playbook.yaml tests/test_config.py
git commit -m "feat: playbook YAML loader with validation errors"
```

---

## Task 4: Superset client (`src/superset_client.py`)

**Files:**
- Create: `src/superset_client.py`
- Create: `tests/test_superset_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_superset_client.py
import pytest
import pytest_asyncio
import httpx
from pytest_httpx import HTTPXMock
from src.superset_client import SupersetClient, SupersetError


BASE = "https://superset.example.com"
CREDS = {"username": "user", "password": "pass"}


@pytest.fixture
def client():
    return SupersetClient(base_url=BASE, username="user", password="pass")


@pytest.mark.asyncio
async def test_get_chart_data_success(client, httpx_mock: HTTPXMock):
    # mock login
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/api/v1/security/login",
        json={"access_token": "tok123"},
    )
    # mock chart data
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/api/v1/chart/data",
        json={"result": [{"data": [{"success_rate": 99.1, "prev_success_rate": 98.8}]}]},
    )
    result = await client.get_chart_data(412)
    assert result["result"][0]["data"][0]["success_rate"] == 99.1


@pytest.mark.asyncio
async def test_get_chart_data_retries_on_timeout(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/api/v1/security/login",
        json={"access_token": "tok123"},
    )
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"))
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/api/v1/chart/data",
        json={"result": [{"data": []}]},
    )
    result = await client.get_chart_data(412)
    assert result["result"] == [{"data": []}]


@pytest.mark.asyncio
async def test_get_chart_data_raises_after_max_retries(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/api/v1/security/login",
        json={"access_token": "tok123"},
    )
    for _ in range(3):
        httpx_mock.add_exception(httpx.ReadTimeout("timeout"))
    with pytest.raises(SupersetError, match="timeout"):
        await client.get_chart_data(412)


@pytest.mark.asyncio
async def test_list_charts_success(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/api/v1/security/login",
        json={"access_token": "tok123"},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/api/v1/dashboard/12/charts",
        json={"result": [{"id": 415, "slice_name": "Success by Method"}]},
    )
    charts = await client.list_charts(dashboard_id=12)
    assert charts[0]["id"] == 415
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_superset_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.superset_client'`

- [ ] **Step 3: Implement `src/superset_client.py`**

```python
import asyncio
import httpx
from typing import Any


class SupersetError(Exception):
    pass


class SupersetClient:
    def __init__(self, base_url: str, username: str, password: str, retries: int = 2):
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._retries = retries
        self._token: str | None = None

    async def _login(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            f"{self._base}/api/v1/security/login",
            json={"username": self._username, "password": self._password,
                  "provider": "db", "refresh": True},
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def _request_with_retry(self, method: str, url: str, **kwargs) -> Any:
        async with httpx.AsyncClient(timeout=30) as client:
            await self._login(client)
            last_exc: Exception | None = None
            for attempt in range(self._retries + 1):
                try:
                    resp = await client.request(
                        method, url, headers=self._auth_headers(), **kwargs
                    )
                    resp.raise_for_status()
                    return resp.json()
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_exc = e
                    if attempt < self._retries:
                        await asyncio.sleep(1.5 ** attempt)
            raise SupersetError(f"Superset request failed after retries: {last_exc}") from last_exc

    async def get_chart_data(self, chart_id: int) -> dict:
        return await self._request_with_retry(
            "POST",
            f"{self._base}/api/v1/chart/data",
            json={"datasource": {"id": chart_id, "type": "table"},
                  "queries": [{"row_limit": 1000}]},
        )

    async def list_charts(self, dashboard_id: int) -> list[dict]:
        result = await self._request_with_retry(
            "GET",
            f"{self._base}/api/v1/dashboard/{dashboard_id}/charts",
        )
        return result.get("result", [])
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_superset_client.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/superset_client.py tests/test_superset_client.py
git commit -m "feat: Superset REST client with retry + list_charts"
```

---

## Task 5: Rule engine (`src/rule_engine.py`)

**Files:**
- Create: `src/rule_engine.py`
- Create: `tests/test_rule_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_rule_engine.py
import pytest
from src.config import ThresholdRule, DeviationRule
from src.rule_engine import evaluate_rules, RuleResult


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
    # chart_data contains previous value keyed by compare_to convention
    chart_data = {"previous_value": 98.5}
    result = evaluate_rules([rule], current=95.2, chart_data=chart_data)
    assert result.is_abnormal is True
    drop_str = result.triggered_rules[0]
    assert "3.35" in drop_str or "3.3" in drop_str  # ~3.35% drop


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
    # threshold passes (99.1 >= 98) but deviation breaches
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
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_rule_engine.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.rule_engine'`

- [ ] **Step 3: Implement `src/rule_engine.py`**

```python
from dataclasses import dataclass, field
from src.config import Rule, ThresholdRule, DeviationRule

BASELINE_KEYS = {
    "yesterday": "previous_value",
    "last_week": "last_week_value",
    "7d_avg": "seven_day_avg",
}

OPS = {
    ">=": lambda a, b: a < b,   # breach if current does NOT satisfy
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
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_rule_engine.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rule_engine.py tests/test_rule_engine.py
git commit -m "feat: rule engine (threshold + deviation, all ops)"
```

---

## Task 6: Monitoring engine (`src/engine.py`)

**Files:**
- Create: `src/engine.py`
- Create: `tests/test_engine.py`

The engine is a deterministic loop: for each check, fetch the summary chart, extract the metric value, evaluate rules, and if abnormal, fetch each drilldown chart. It returns a list of `CheckResult` objects.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_engine.py
import pytest
from unittest.mock import AsyncMock, patch
from src.config import Playbook
from src.engine import run_engine, CheckResult


PLAYBOOK_RAW = {
    "checks": [
        {
            "id": "sr",
            "name": "Success Rate",
            "summary_chart_id": 412,
            "metric": "success_rate",
            "rules": [{"type": "threshold", "op": ">=", "value": 98.0}],
            "drilldown": [{"chart_id": 415, "describe": "by method"}],
            "deep_dive": "off",
            "severity": "high",
        }
    ]
}


@pytest.mark.asyncio
async def test_normal_check_no_drilldown():
    playbook = Playbook.model_validate(PLAYBOOK_RAW)
    mock_client = AsyncMock()
    mock_client.get_chart_data.return_value = {
        "result": [{"data": [{"success_rate": 99.5}]}]
    }
    results = await run_engine(playbook, mock_client)
    assert len(results) == 1
    assert results[0].is_abnormal is False
    assert results[0].check_id == "sr"
    mock_client.get_chart_data.assert_called_once_with(412)


@pytest.mark.asyncio
async def test_abnormal_check_fetches_drilldown():
    playbook = Playbook.model_validate(PLAYBOOK_RAW)
    mock_client = AsyncMock()
    mock_client.get_chart_data.side_effect = [
        {"result": [{"data": [{"success_rate": 95.2}]}]},   # summary
        {"result": [{"data": [{"method": "qr", "rate": 80.1}]}]},  # drilldown
    ]
    results = await run_engine(playbook, mock_client)
    assert results[0].is_abnormal is True
    assert results[0].drilldown_data is not None
    assert len(results[0].drilldown_data) == 1
    assert mock_client.get_chart_data.call_count == 2


@pytest.mark.asyncio
async def test_superset_error_isolates_check():
    from src.superset_client import SupersetError
    playbook = Playbook.model_validate(PLAYBOOK_RAW)
    mock_client = AsyncMock()
    mock_client.get_chart_data.side_effect = SupersetError("timeout")
    results = await run_engine(playbook, mock_client)
    assert results[0].error == "timeout"
    assert results[0].is_abnormal is False


@pytest.mark.asyncio
async def test_missing_metric_marks_error():
    playbook = Playbook.model_validate(PLAYBOOK_RAW)
    mock_client = AsyncMock()
    mock_client.get_chart_data.return_value = {
        "result": [{"data": [{"other_metric": 42}]}]  # success_rate absent
    }
    results = await run_engine(playbook, mock_client)
    assert results[0].error is not None
    assert "success_rate" in results[0].error
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_engine.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.engine'`

- [ ] **Step 3: Implement `src/engine.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from src.config import Playbook, Check
from src.superset_client import SupersetClient, SupersetError
from src.rule_engine import evaluate_rules, RuleResult

BASELINE_KEYS_FOR_CHART = {
    "yesterday": "previous_value",
    "last_week": "last_week_value",
    "7d_avg": "seven_day_avg",
}


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
    """Return (current_value, flat_row_dict). Raises KeyError if metric absent."""
    rows: list[dict] = []
    for result_item in data.get("result", []):
        rows.extend(result_item.get("data", []))
    if not rows:
        raise KeyError(f"No rows in chart data for metric '{metric}'")
    row = rows[0]
    if metric not in row:
        raise KeyError(f"Metric '{metric}' not found in chart data. Available: {list(row.keys())}")
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
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_engine.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/engine.py tests/test_engine.py
git commit -m "feat: deterministic monitoring engine loop"
```

---

## Task 7: LLM reporter (`src/llm_reporter.py`)

**Files:**
- Create: `src/llm_reporter.py`
- Create: `tests/test_llm_reporter.py`

The reporter calls the LLM once per abnormal finding and gets back a `ReasonResponse` with `reason` (plain language) and `confident` (bool) + `confidence_note`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm_reporter.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.engine import CheckResult, DrilldownResult, RuleResult
from src.llm_reporter import LLMReporter, ReasonResponse


def _make_abnormal_result() -> CheckResult:
    cr = CheckResult(check_id="sr", name="Success Rate", severity="high")
    cr.is_abnormal = True
    cr.current_value = 95.2
    cr.rule_result = RuleResult(
        is_abnormal=True,
        triggered_rules=["threshold >= 98.0: current=95.2"],
    )
    cr.drilldown_data = [
        DrilldownResult(
            chart_id=415,
            describe="success rate by payment method",
            data={"result": [{"data": [{"method": "bank_transfer", "rate": 78.0}]}]},
        )
    ]
    return cr


@pytest.mark.asyncio
async def test_reporter_returns_reason_and_confidence():
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"reason": "Drop driven by bank_transfer failures.", "confident": true, "confidence_note": ""}'
    )
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

    reporter = LLMReporter(client=mock_openai, model="gpt-4o")
    result = await reporter.explain(check_result=_make_abnormal_result())

    assert isinstance(result, ReasonResponse)
    assert result.confident is True
    assert "bank_transfer" in result.reason


@pytest.mark.asyncio
async def test_reporter_fallback_on_llm_error():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock(side_effect=Exception("LLM down"))

    reporter = LLMReporter(client=mock_openai, model="gpt-4o")
    result = await reporter.explain(check_result=_make_abnormal_result())

    assert result.reason != ""
    assert result.confident is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_reporter_handles_malformed_json():
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "not json at all"
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

    reporter = LLMReporter(client=mock_openai, model="gpt-4o")
    result = await reporter.explain(check_result=_make_abnormal_result())

    assert result.confident is False
    assert result.error is not None
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_llm_reporter.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.llm_reporter'`

- [ ] **Step 3: Implement `src/llm_reporter.py`**

```python
from __future__ import annotations
import json
from dataclasses import dataclass
from openai import AsyncOpenAI
from src.engine import CheckResult


@dataclass
class ReasonResponse:
    reason: str
    confident: bool
    confidence_note: str = ""
    error: str | None = None


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
        reason=f"{cr.name} is abnormal. Triggered: {'; '.join(rules)}. "
               f"Current value: {cr.current_value}.",
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
            return ReasonResponse(reason="", confident=False,
                                  error=f"LLM returned invalid JSON: {e}")
        except Exception as e:
            return _fallback_reason(check_result)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_llm_reporter.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/llm_reporter.py tests/test_llm_reporter.py
git commit -m "feat: LLM reporter with confidence self-assessment and fallback"
```

---

## Task 8: Deep-dive investigator (`src/deep_dive.py`)

**Files:**
- Create: `src/deep_dive.py`
- Create: `tests/test_deep_dive.py`

The deep-dive loop lets the LLM pick additional charts (within scope/budget) when `confident=False`. Each step: LLM picks a tool call (`list_charts` or `get_chart_data`), we execute it, append to the trail, and re-ask.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_deep_dive.py
import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from src.config import DeepDiveConfig, DeepDiveScope
from src.engine import CheckResult, DrilldownResult, RuleResult
from src.deep_dive import DeepDiveInvestigator, DeepDiveResult


def _make_config(dashboard_ids=None, max_extra_charts=3, max_steps=4):
    return DeepDiveConfig(
        enabled="auto",
        trigger="low_confidence",
        max_extra_charts=max_extra_charts,
        max_steps=max_steps,
        scope=DeepDiveScope(dashboard_ids=dashboard_ids or [12]),
    )


def _make_check_result():
    cr = CheckResult(check_id="sr", name="Success Rate", severity="high")
    cr.is_abnormal = True
    cr.current_value = 95.2
    cr.rule_result = RuleResult(is_abnormal=True, triggered_rules=["threshold >= 98.0"])
    cr.drilldown_data = []
    return cr


@pytest.mark.asyncio
async def test_deep_dive_exits_when_confident_after_one_step():
    mock_superset = AsyncMock()
    mock_superset.list_charts.return_value = [{"id": 430, "slice_name": "Txn by Bank"}]
    mock_superset.get_chart_data.return_value = {"result": [{"data": [{"bank": "X", "rate": 70}]}]}

    mock_llm = MagicMock()
    # First call: not confident, picks get_chart_data
    # Second call: confident
    mock_llm.chat.completions.create = AsyncMock(side_effect=[
        _tool_response("get_chart_data", {"chart_id": 430}),
        _confident_response("Bank X caused the drop."),
    ])

    investigator = DeepDiveInvestigator(
        superset=mock_superset, llm_client=mock_llm, model="gpt-4o"
    )
    result = await investigator.investigate(
        check_result=_make_check_result(), config=_make_config()
    )
    assert result.final_reason != ""
    assert result.extra_charts_examined == 1
    assert result.was_confident is True


@pytest.mark.asyncio
async def test_deep_dive_respects_max_extra_charts_budget():
    mock_superset = AsyncMock()
    mock_superset.get_chart_data.return_value = {"result": [{"data": []}]}

    # LLM always wants more charts — should stop at budget
    calls = [_tool_response("get_chart_data", {"chart_id": 430 + i}) for i in range(10)]
    mock_llm = MagicMock()
    mock_llm.chat.completions.create = AsyncMock(side_effect=calls)

    investigator = DeepDiveInvestigator(
        superset=mock_superset, llm_client=mock_llm, model="gpt-4o"
    )
    result = await investigator.investigate(
        check_result=_make_check_result(), config=_make_config(max_extra_charts=2, max_steps=10)
    )
    assert result.extra_charts_examined <= 2


@pytest.mark.asyncio
async def test_deep_dive_refuses_out_of_scope_dashboard():
    mock_superset = AsyncMock()
    mock_superset.list_charts.return_value = []

    mock_llm = MagicMock()
    # LLM asks for dashboard 99 which is NOT in scope (scope=[12])
    mock_llm.chat.completions.create = AsyncMock(side_effect=[
        _tool_response("list_charts", {"dashboard_id": 99}),
        _confident_response("Could not determine cause."),
    ])

    investigator = DeepDiveInvestigator(
        superset=mock_superset, llm_client=mock_llm, model="gpt-4o"
    )
    result = await investigator.investigate(
        check_result=_make_check_result(), config=_make_config(dashboard_ids=[12])
    )
    mock_superset.list_charts.assert_not_called()
    assert "out of scope" in result.audit_log[-1].lower() or result.extra_charts_examined == 0


@pytest.mark.asyncio
async def test_deep_dive_fallback_on_superset_error():
    from src.superset_client import SupersetError
    mock_superset = AsyncMock()
    mock_superset.get_chart_data.side_effect = SupersetError("timeout")

    mock_llm = MagicMock()
    mock_llm.chat.completions.create = AsyncMock(side_effect=[
        _tool_response("get_chart_data", {"chart_id": 430}),
        _confident_response("Best effort."),
    ])

    investigator = DeepDiveInvestigator(
        superset=mock_superset, llm_client=mock_llm, model="gpt-4o"
    )
    result = await investigator.investigate(
        check_result=_make_check_result(), config=_make_config()
    )
    assert result.final_reason != ""  # never raises


# ---- helpers ----

def _tool_response(tool_name: str, args: dict):
    msg = MagicMock()
    msg.choices[0].message.content = json.dumps({
        "action": tool_name,
        "args": args,
    })
    return msg


def _confident_response(reason: str):
    msg = MagicMock()
    msg.choices[0].message.content = json.dumps({
        "action": "done",
        "reason": reason,
        "confident": True,
    })
    return msg
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_deep_dive.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.deep_dive'`

- [ ] **Step 3: Implement `src/deep_dive.py`**

```python
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

    async def investigate(self, check_result: CheckResult, config: DeepDiveConfig) -> DeepDiveResult:
        allowed = set(config.scope.dashboard_ids)
        budget_charts = config.max_extra_charts
        budget_steps = config.max_steps
        audit: list[str] = []
        extra_charts = 0
        steps = 0
        context = _summarize_check(check_result)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT +
             f"\nAllowed dashboard IDs: {list(allowed)}"},
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

                elif action == "list_charts":
                    dashboard_id = parsed["args"]["dashboard_id"]
                    if dashboard_id not in allowed:
                        audit.append(f"step {steps}: list_charts(dashboard_id={dashboard_id}) refused — out of scope")
                        messages.append({"role": "user",
                                         "content": f"Error: dashboard {dashboard_id} is out of scope."})
                        continue
                    try:
                        charts = await self._superset.list_charts(dashboard_id)
                        audit.append(f"step {steps}: list_charts(dashboard_id={dashboard_id}) → {len(charts)} charts")
                        messages.append({"role": "user",
                                         "content": f"Charts on dashboard {dashboard_id}: {json.dumps(charts, default=str)[:1500]}"})
                    except SupersetError as e:
                        audit.append(f"step {steps}: list_charts error: {e}")
                        messages.append({"role": "user", "content": f"Error fetching charts: {e}"})

                elif action == "get_chart_data":
                    if extra_charts >= budget_charts:
                        audit.append(f"step {steps}: budget exhausted ({budget_charts} extra charts)")
                        break
                    chart_id = parsed["args"]["chart_id"]
                    try:
                        data = await self._superset.get_chart_data(chart_id)
                        extra_charts += 1
                        audit.append(f"step {steps}: get_chart_data(chart_id={chart_id}) → OK")
                        messages.append({"role": "user",
                                         "content": f"Chart {chart_id} data: {json.dumps(data, default=str)[:1500]}"})
                    except SupersetError as e:
                        audit.append(f"step {steps}: get_chart_data({chart_id}) error: {e}")
                        messages.append({"role": "user", "content": f"Error fetching chart {chart_id}: {e}"})

            audit.append("deep-dive loop ended (budget or step limit reached)")
            # Ask LLM for best-effort conclusion
            messages.append({"role": "user",
                              "content": 'Budget reached. Provide your best explanation now using the data collected. Respond with {"action": "done", "reason": "...", "confident": false}.'})
            resp = await self._llm.chat.completions.create(
                model=self._model, messages=messages, temperature=0
            )
            parsed = json.loads(resp.choices[0].message.content)
            return DeepDiveResult(
                final_reason=parsed.get("reason", "Could not determine root cause within budget."),
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
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_deep_dive.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/deep_dive.py tests/test_deep_dive.py
git commit -m "feat: bounded LLM deep-dive investigator with scope/budget guardrails"
```

---

## Task 9: Report builder (`src/report_builder.py`)

**Files:**
- Create: `src/report_builder.py`
- Create: `tests/test_report_builder.py`

Assembles `CheckResult` + reasons into the final JSON the Teams Workflow renders.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_report_builder.py
import pytest
from src.engine import CheckResult, RuleResult
from src.llm_reporter import ReasonResponse
from src.report_builder import build_report


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
        triggered_rules=["threshold >= 98.0: current=95.2"]
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
    assert report["anomalies"][0]["check_id"] == "sr"  # high first


def test_error_check_surfaced():
    cr = CheckResult(check_id="sr", name="Success Rate", severity="high")
    cr.error = "chart 412 timeout"
    report = build_report(results=[cr], reasons={}, run_ts="2026-06-11T08:00:00")
    assert len(report["errors"]) == 1
    assert "timeout" in report["errors"][0]["message"]
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_report_builder.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.report_builder'`

- [ ] **Step 3: Implement `src/report_builder.py`**

```python
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
        anomalies.append({
            "check_id": cr.check_id,
            "name": cr.name,
            "severity": cr.severity,
            "current_value": cr.current_value,
            "triggered_rules": cr.rule_result.triggered_rules if cr.rule_result else [],
            "skipped_rules": cr.rule_result.skipped_rules if cr.rule_result else [],
            "reason": reason_resp.reason if reason_resp else "",
            "deep_dive_tag": (
                f"deep-dive: examined {cr.deep_dive_charts_examined} extra charts"
                if cr.deep_dive_used else None
            ),
        })

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
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_report_builder.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/report_builder.py tests/test_report_builder.py
git commit -m "feat: report builder — assemble findings into Teams-ready JSON"
```

---

## Task 10: Run log (`src/run_log.py`)

**Files:**
- Create: `src/run_log.py`

Simple structured JSON lines logger — one file per run, one JSON object per line.

- [ ] **Step 1: Implement `src/run_log.py`** (no separate test file; covered by integration)

```python
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class RunLog:
    def __init__(self, log_dir: str, run_ts: str):
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        safe_ts = run_ts.replace(":", "-").replace("T", "_")
        self._path = Path(log_dir) / f"run_{safe_ts}.jsonl"
        self._f = self._path.open("a", encoding="utf-8")

    def write(self, event: str, **data):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **data,
        }
        self._f.write(json.dumps(record, default=str) + "\n")
        self._f.flush()

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
```

- [ ] **Step 2: Commit**

```bash
git add src/run_log.py
git commit -m "feat: structured run log (JSON lines, one file per run)"
```

---

## Task 11: Wire everything — FastAPI app + dry-run (`src/main.py`)

**Files:**
- Create: `src/main.py`

This is the top-level entrypoint. It creates one `/run` POST endpoint (protected by a bearer token), and supports `--dry-run` via CLI for local validation.

- [ ] **Step 1: Implement `src/main.py`**

```python
from __future__ import annotations
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from openai import AsyncOpenAI

from src.config import DeepDiveConfig
from src.deep_dive import DeepDiveInvestigator
from src.engine import run_engine, CheckResult
from src.llm_reporter import LLMReporter
from src.playbook import load_playbook, PlaybookLoadError
from src.report_builder import build_report
from src.run_log import RunLog
from src.superset_client import SupersetClient, SupersetError

load_dotenv()

app = FastAPI(title="Dashboard Monitoring Assistant")
_bearer = HTTPBearer()


def _get_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _should_deep_dive(cr: CheckResult, check_deep_dive: str, dd_config: DeepDiveConfig,
                      reason_confident: bool) -> bool:
    if dd_config.enabled == "off" or check_deep_dive == "off":
        return False
    trigger = dd_config.trigger
    if trigger == "always":
        return True
    if trigger == "high_severity" and cr.severity == "high":
        return True
    if trigger == "low_confidence" and not reason_confident:
        return True
    return False


async def _run(dry_run: bool = False) -> dict:
    playbook_path = os.getenv("PLAYBOOK_PATH", "playbook.yaml")
    log_dir = os.getenv("RUN_LOG_DIR", "./logs")
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    playbook = load_playbook(playbook_path)

    superset = SupersetClient(
        base_url=_get_env("SUPERSET_BASE_URL"),
        username=_get_env("SUPERSET_USERNAME"),
        password=_get_env("SUPERSET_PASSWORD"),
    )
    llm_client = AsyncOpenAI(
        base_url=_get_env("LLM_BASE_URL"),
        api_key=_get_env("LLM_API_KEY"),
    )
    model = os.getenv("LLM_MODEL", "gpt-4o")
    reporter = LLMReporter(client=llm_client, model=model)
    investigator = DeepDiveInvestigator(superset=superset, llm_client=llm_client, model=model)

    with RunLog(log_dir=log_dir, run_ts=run_ts) as log:
        log.write("run_start", playbook_path=playbook_path, dry_run=dry_run,
                  checks=len(playbook.checks))

        results = await run_engine(playbook, superset)

        reasons = {}
        for cr, check in zip(results, playbook.checks):
            log.write("check_complete", check_id=cr.check_id, is_abnormal=cr.is_abnormal,
                      error=cr.error)
            if not cr.is_abnormal or cr.error:
                continue

            reason_resp = await reporter.explain(cr)
            log.write("llm_reason", check_id=cr.check_id, confident=reason_resp.confident,
                      reason=reason_resp.reason[:200])

            if _should_deep_dive(cr, check.deep_dive, playbook.deep_dive, reason_resp.confident):
                dd_result = await investigator.investigate(cr, playbook.deep_dive)
                log.write("deep_dive", check_id=cr.check_id,
                          steps=dd_result.steps_taken,
                          extra_charts=dd_result.extra_charts_examined,
                          audit=dd_result.audit_log)
                cr.deep_dive_used = True
                cr.deep_dive_charts_examined = dd_result.extra_charts_examined
                reason_resp.reason = dd_result.final_reason

            reasons[cr.check_id] = reason_resp

        report = build_report(results=results, reasons=reasons, run_ts=run_ts)
        log.write("run_complete", status=report["status"],
                  anomaly_count=report["anomaly_count"])

    return report


@app.post("/run")
async def run_endpoint(credentials: HTTPAuthorizationCredentials = Security(_bearer)):
    expected = os.getenv("API_TOKEN", "")
    if not expected or credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        return await _run()
    except PlaybookLoadError as e:
        raise HTTPException(status_code=500, detail=f"Playbook error: {e}")
    except SupersetError as e:
        raise HTTPException(status_code=502, detail=f"Superset unreachable: {e}")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── CLI dry-run ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        report = asyncio.run(_run(dry_run=True))
        print(json.dumps(report, indent=2, default=str))
    else:
        import uvicorn
        uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=False)
```

- [ ] **Step 2: Verify the app starts**

```bash
cd /Users/lap15954-local/Data/nck-claw-a-thon
pip install -r requirements.txt -q
python -c "from src.main import app; print('import OK')"
```

Expected: `import OK`

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/main.py
git commit -m "feat: FastAPI /run endpoint + --dry-run CLI, wires all components"
```

---

## Task 12: AgentBase deployment

**Files:**
- No code changes — deployment steps.

- [ ] **Step 1: Read the AgentBase deploy skill**

Read `/Users/lap15954-local/.cursor/skills/agentbase-deploy/SKILL.md` and follow Part 1 (build, push, create runtime).

- [ ] **Step 2: Set up agent identity (Superset credentials)**

Read `/Users/lap15954-local/.cursor/skills/agentbase-identity/SKILL.md` and store `SUPERSET_BASE_URL`, `SUPERSET_USERNAME`, `SUPERSET_PASSWORD` as secrets.

- [ ] **Step 3: Set up LLM key**

Read `/Users/lap15954-local/.cursor/skills/agentbase-llm/SKILL.md` and provision the platform LLM API key (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`).

- [ ] **Step 4: Configure the runtime env vars**

Set on the AgentBase runtime:
```
PLAYBOOK_PATH=/app/playbook.yaml
RUN_LOG_DIR=/app/logs
API_TOKEN=<generate a random token for Teams Workflow>
```

- [ ] **Step 5: Verify deployment**

```bash
curl -s -X POST https://<your-agentbase-runtime-url>/run \
  -H "Authorization: Bearer <API_TOKEN>" | jq .status
```

Expected: `"all_clear"` or `"issues_found"` (not an error).

- [ ] **Step 6: Teams Workflow — Workflow 1 (on-demand "run now")**

Give your admin team these exact steps:
1. In Teams, go to a channel → click **...** → **Workflows**.
2. Create new → search **"When a keyword is mentioned"** template.
3. Keyword: `run report`  
4. Add action: **HTTP** → Method: `POST`, URL: `https://<runtime-url>/run`, Headers: `Authorization: Bearer <API_TOKEN>`.
5. Add action: **Post card in a chat or channel** → channel: your monitoring channel, body: the response body from the HTTP step.

- [ ] **Step 7: Teams Workflow — Workflow 2 (scheduled)**

1. In Teams → **Workflows** → Create new → **Scheduled** (Recurrence trigger).
2. Set recurrence: daily at `08:00` your timezone.
3. Same HTTP action and Post card action as Workflow 1.

- [ ] **Step 8: Edit `playbook.yaml` with your real chart IDs**

Replace the example `chart_id` values with your actual Superset chart IDs, set your `scope.dashboard_ids`, thresholds, and severity.

- [ ] **Step 9: Run dry-run against real Superset**

```bash
python -m src.main --dry-run
```

Review the JSON output against your expectations before relying on the Teams reports.

- [ ] **Step 10: Commit final playbook**

```bash
git add playbook.yaml
git commit -m "config: initial monitoring playbook with real chart IDs"
git push origin main
```

---

## Self-Review: Spec Coverage

| Spec requirement | Task(s) |
|---|---|
| Scheduled daily run + on-demand from Teams | Task 11 (`/run` endpoint), Task 12 (Workflows) |
| Read metric values from Superset charts | Task 4, 6 |
| Threshold + deviation rules | Task 5 |
| Ordered multi-chart drill-down | Task 6 |
| Baseline from chart only; skip if absent | Task 5 (`skipped_rules`) |
| Bounded LLM deep-dive (scope, budget, read-only) | Task 8 |
| Low-confidence trigger | Task 11 (`_should_deep_dive`) |
| Plain-language reason + confidence | Task 7 |
| Report: verdict first, numbers, reason, deep-dive tag, checked footer | Task 9 |
| Per-check isolation (errors don't stop run) | Task 6 (`try/except`) |
| Superset retry + unreachable handling | Task 4 |
| LLM fallback (raw findings if LLM fails) | Task 7 |
| Deep-dive fail-safe (never blocks run) | Task 8 |
| Run log (audit) | Task 10, 11 |
| Config validation on startup | Task 2, 3 |
| `--dry-run` mode | Task 11 |
| AgentBase deploy + Teams Workflow steps | Task 12 |
