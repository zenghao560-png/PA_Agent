"""Stable models for theoretical order replay results."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReplayStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    WAITING_ENTRY = "waiting_entry"
    ENTRY_TRIGGERED = "entry_triggered"
    ENTRY_EXPIRED = "entry_expired"
    ENTRY_INVALIDATED = "entry_invalidated"
    TARGET_1 = "target_1"
    STOP = "stop"
    HOLDING_EXPIRED = "holding_expired"
    ENTRY_AMBIGUOUS = "entry_ambiguous"
    EXIT_AMBIGUOUS = "exit_ambiguous"
    DATA_INSUFFICIENT = "data_insufficient"


class ReplayResult(BaseModel):
    """A replay result. It never claims an exchange fill."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    status: ReplayStatus
    result_kind: str = "order_replay"
    policy_version: str = "replay-v1"
    order_type: str | None = None
    order_direction: str | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    entry_time: float | None = None
    exit_time: float | None = None
    entry_wait_bars: int | None = None
    holding_bars: int | None = None
    result_r: float | None = None
    trigger_mode: str | None = None
    reason: str = ""
    source_record: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
