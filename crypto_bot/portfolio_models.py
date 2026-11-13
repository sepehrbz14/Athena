"""Typed Phase 4 configuration and orchestration state.

Accounting positions and completed accounting records remain owned by
``portfolio_ledger``. These dataclasses hold only signal, order, execution
policy, and reporting metadata around that ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
import hashlib
import json

from jsonschema import Draft202012Validator

from .execution import BarExecutionModel
from .models import Side
from .portfolio_ledger import D


def parse_utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("portfolio timestamps must be timezone-aware UTC")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    config_id: str
    initial_equity_usdt: Decimal
    instrument_allowlist: tuple[str, ...]
    per_trade_risk_pct: Decimal
    aggregate_open_pending_stop_risk_pct: Decimal
    leverage: Decimal
    maximum_gross_exposure_equity_multiple: Decimal
    maximum_net_exposure_equity_multiple: Decimal
    maximum_active_positions: int
    pyramiding_enabled: bool
    minimum_equity_usdt: Decimal
    cash_buffer_pct: Decimal
    minimum_quantity_btc: Decimal
    priority_strategy_order: tuple[str, ...]
    daily_loss_limit_pct: Decimal
    maximum_drawdown_pct: Decimal
    pending_order_expiry_eligible_candles: int
    execution: BarExecutionModel
    force_close_at_end: bool
    raw_config: dict[str, Any] = field(repr=False, compare=False)
    sha256: str

    @classmethod
    def from_path(cls, path: str | Path, schema_path: str | Path | None = None) -> "PortfolioConfig":
        config_path = Path(path)
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        schema_file = Path(schema_path) if schema_path else Path(__file__).resolve().parents[1] / "schemas" / "athena2_portfolio_config_v1.schema.json"
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(raw)
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        execution_data = {key: raw["execution"][key] for key in (
            "fee_rate", "slippage_bps", "funding_rate_per_8h", "candle_minutes", "max_participation_rate"
        )}
        return cls(
            config_id=raw["config_id"], initial_equity_usdt=D(raw["initial_equity_usdt"]),
            instrument_allowlist=tuple(raw["instrument_allowlist"]),
            per_trade_risk_pct=D(raw["per_trade_risk_pct"]),
            aggregate_open_pending_stop_risk_pct=D(raw["aggregate_open_pending_stop_risk_pct"]),
            leverage=D(raw["leverage"]),
            maximum_gross_exposure_equity_multiple=D(raw["maximum_gross_exposure_equity_multiple"]),
            maximum_net_exposure_equity_multiple=D(raw["maximum_net_exposure_equity_multiple"]),
            maximum_active_positions=int(raw["maximum_active_positions"]),
            pyramiding_enabled=bool(raw["pyramiding_enabled"]),
            minimum_equity_usdt=D(raw["minimum_equity_usdt"]), cash_buffer_pct=D(raw["cash_buffer_pct"]),
            minimum_quantity_btc=D(raw["minimum_quantity_btc"]),
            priority_strategy_order=tuple(raw["priority_strategy_order"]),
            daily_loss_limit_pct=D(raw["daily_loss_limit_pct"]),
            maximum_drawdown_pct=D(raw["maximum_drawdown_pct"]),
            pending_order_expiry_eligible_candles=int(raw["pending_order_expiry_eligible_candles"]),
            execution=BarExecutionModel(**execution_data), force_close_at_end=bool(raw["execution"]["force_close_at_end"]),
            raw_config=raw, sha256=hashlib.sha256(canonical).hexdigest(),
        )

    def strategy_rank(self, strategy: str) -> tuple[int, str]:
        try:
            return self.priority_strategy_order.index(strategy), strategy
        except ValueError:
            return len(self.priority_strategy_order), strategy


@dataclass(frozen=True, slots=True)
class FrozenSignal:
    signal_id: int
    instrument_id: str
    candle_open_time: datetime
    available_at: datetime
    strategy: str
    side: Side
    confidence: Decimal
    reference_price: Decimal
    stop_price: Decimal
    take_profit: Decimal | None
    source_signal_sha256: str
    source_candle_sha256: str
    candle_index: int

    @property
    def position_id(self) -> str:
        return f"position:{self.instrument_id}:{self.signal_id}"

    @property
    def order_id(self) -> str:
        return f"order:{self.instrument_id}:{self.signal_id}"


@dataclass(frozen=True, slots=True)
class SignalDisposition:
    raw_row: int
    signal_id: int | None
    instrument_id: str | None
    available_at: datetime | None
    strategy: str | None
    selected: bool | None
    status: str
    reason_code: str | None = None
    order_id: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "raw_row": self.raw_row, "signal_id": self.signal_id, "instrument_id": self.instrument_id,
            "available_at": self.available_at.isoformat() if self.available_at else None,
            "strategy": self.strategy, "ensemble_selected": self.selected,
            "status": self.status, "reason_code": self.reason_code, "order_id": self.order_id,
        }


@dataclass(slots=True)
class PortfolioOrder:
    order_id: str
    signal: FrozenSignal
    accepted_at: datetime
    requested_quantity: Decimal
    remaining_quantity: Decimal
    reserved_collateral: Decimal
    reserved_stop_risk: Decimal
    eligible_attempts: int = 0


@dataclass(slots=True)
class PortfolioPositionMeta:
    position_id: str
    order_id: str
    signal: FrozenSignal
    stop_price: Decimal
    take_profit: Decimal | None
    opened_at: datetime
    last_funding_timestamp: datetime
    entry_fill_price: Decimal
    exit_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioEvent:
    event_id: str
    event_type: str
    event_time: datetime
    phase: str
    account_id: str
    instrument_id: str | None = None
    source_signal_id: int | None = None
    order_id: str | None = None
    position_id: str | None = None
    reason_code: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": "athena.portfolio.event.v1", "event_id": self.event_id,
            "event_type": self.event_type, "event_time": self.event_time.isoformat(),
            "phase": self.phase, "account_id": self.account_id, "instrument_id": self.instrument_id,
            "source_signal_id": self.source_signal_id, "order_id": self.order_id,
            "position_id": self.position_id, "reason_code": self.reason_code, "payload": self.payload,
        }
