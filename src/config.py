from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


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
