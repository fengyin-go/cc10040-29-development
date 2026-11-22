"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class WindowPayload(BaseModel):
    window_id: str
    window_start: str
    window_end: str


class SamplePayload(BaseModel):
    metric_name: str
    value: float
    sample_time: str
    reported_at: str | None = None


class BatchInspectionPayload(BaseModel):
    window_id: str
    rule_ids: list[int] | None = None
    batch_id: str | None = None
