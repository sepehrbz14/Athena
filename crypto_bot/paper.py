"""Paper-only, deterministic replay runner for completed 15-minute candles.

This runner has no exchange client and cannot submit orders. It stores the
prospective candle journal, then reconstructs the paper account from the frozen
historical warm-up plus that journal after each accepted candle. Replays are
therefore restart-safe and idempotent, at the cost of replaying history.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .backtest import BacktestConfig, IntrabarBacktester
from .controls import KillSwitch, KillSwitchConfig, KillSwitchMode
from .diagnostics import LogLevel
from .models import BacktestResult, Candle
from .reporting import assert_trade_reconciliation, normalize_trade
from .risk import PositionSizer, RiskConfig
from .strategies import StrategyEnsemble


UTC = timezone.utc
CANDLE_INTERVAL = timedelta(minutes=15)
MAX_INGESTION_LATENESS = timedelta(minutes=2)
INITIAL_EQUITY = 10_000.0
RISK_PER_TRADE = 0.005
LEVERAGE = 2.0
FEE_RATE = 0.0005
SLIPPAGE_BPS = 5.0
FUNDING_RATE_PER_8H = 0.0001
PARTICIPATION_RATE = 0.05
SIZING_COST_RESERVE = 0.002


class PaperInputError(ValueError):
    """An input candle is incomplete, late, duplicate-conflicting, or out of order."""


class PaperOnlyRunner:
    """Replay safety-on simulations from immutable warm-up and append-only bars."""

    def __init__(self, warmup_candles: Sequence[Candle], state_dir: str | Path, dataset_sha256: str) -> None:
        if not warmup_candles:
            raise ValueError("frozen historical warm-up candles are required")
        self.warmup = tuple(self._utc_candle(candle) for candle in warmup_candles)
        if any(a.timestamp >= b.timestamp for a, b in zip(self.warmup, self.warmup[1:])):
            raise ValueError("warm-up timestamps must be strictly increasing")
        if len(dataset_sha256) != 64:
            raise ValueError("dataset_sha256 must be a SHA-256 hex digest")
        try:
            int(dataset_sha256, 16)
        except ValueError as error:
            raise ValueError("dataset_sha256 must be a SHA-256 hex digest") from error
        self.dataset_sha256 = dataset_sha256.lower()
        self.state_dir = Path(state_dir)
        if self.state_dir.exists() and any(self.state_dir.iterdir()) and not (self.state_dir / "paper_manifest.json").exists():
            raise PaperInputError("state directory is not empty and has no matching paper manifest; refusing to mix artifacts")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.candle_path = self.state_dir / "prospective_candles.csv"
        self.manifest_path = self.state_dir / "paper_manifest.json"
        self._lock_path = self.state_dir / ".paper_runner.lock"
        self.prospective: list[tuple[Candle, datetime]] = self._load_journal()
        expected_manifest = self._manifest()
        if self.manifest_path.exists():
            existing = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if existing != expected_manifest:
                raise PaperInputError("state directory manifest does not match frozen dataset/configuration")
        else:
            self._atomic_json(self.manifest_path, expected_manifest)
        # Rebuild snapshots on restart so a crash between journaling an input
        # candle and replacing all derived outputs is recoverable.
        if len(self.prospective) >= 2:
            with self._single_writer():
                self._replay_and_export()

    @staticmethod
    def _utc_candle(candle: Candle) -> Candle:
        if candle.timestamp.tzinfo is None or candle.timestamp.utcoffset() is None:
            raise PaperInputError("paper candles must have timezone-aware timestamps")
        return Candle(candle.timestamp.astimezone(UTC), float(candle.open), float(candle.high),
                      float(candle.low), float(candle.close),
                      None if candle.volume is None else float(candle.volume))

    @staticmethod
    def _utc_time(value: datetime, name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise PaperInputError(f"{name} must be timezone-aware")
        return value.astimezone(UTC)

    def _manifest(self) -> dict[str, Any]:
        return {
            "paper_only": True,
            "real_order_submission": False,
            "dataset_sha256": self.dataset_sha256,
            "historical_warmup_last_candle_open": self.warmup[-1].timestamp.isoformat(),
            "first_prospective_candle_open_expected": (self.warmup[-1].timestamp + CANDLE_INTERVAL).isoformat(),
            "candle_interval_minutes": 15,
            "timestamps": "UTC candle-open labels; decision at close; simulated fill at next open",
            "completed_candle_only": True,
            "maximum_ingestion_lateness_seconds": int(MAX_INGESTION_LATENESS.total_seconds()),
            "configuration": {
                "initial_equity": INITIAL_EQUITY,
                "risk_per_trade_pct": RISK_PER_TRADE,
                "leverage": LEVERAGE,
                "max_position_notional_pct": 1.0,
                "max_margin_fraction": 0.8,
                "fee_rate_per_side": FEE_RATE,
                "slippage_bps_per_side": SLIPPAGE_BPS,
                "funding_rate_per_8h": FUNDING_RATE_PER_8H,
                "max_participation_rate": PARTICIPATION_RATE,
                "sizing_round_trip_cost_reserve_pct": SIZING_COST_RESERVE,
                "kill_switch": asdict(KillSwitchConfig()),
                "kill_switch_mode": KillSwitchMode.SAFETY.value,
                "force_close_at_replay_end": False,
            },
            "recovery_model": "append-only candle journal plus deterministic full replay; no account state is carried outside the journal",
        }

    def _load_journal(self) -> list[tuple[Candle, datetime]]:
        if not self.candle_path.exists():
            return []
        loaded: list[tuple[Candle, datetime]] = []
        with self.candle_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                stamp = datetime.fromisoformat(row["timestamp"])
                received_at = datetime.fromisoformat(row["received_at"])
                if stamp.tzinfo is None or received_at.tzinfo is None:
                    raise PaperInputError("persisted paper timestamps must remain timezone-aware")
                candle = Candle(stamp.astimezone(UTC),
                                float(row["open"]), float(row["high"]), float(row["low"]),
                                float(row["close"]), float(row["volume"]) if row["volume"] else None)
                loaded.append((candle, received_at.astimezone(UTC)))
        previous = self.warmup[-1].timestamp
        for candle, _ in loaded:
            if candle.timestamp != previous + CANDLE_INTERVAL:
                raise PaperInputError("persisted prospective candle journal is not strictly contiguous")
            previous = candle.timestamp
        return loaded

    @contextmanager
    def _single_writer(self):
        try:
            descriptor = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise PaperInputError("another paper-runner process holds the state lock; inspect before clearing a stale lock") from error
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.close(descriptor)
            yield
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._lock_path.unlink(missing_ok=True)

    def append_completed_candle(self, candle: Candle, observed_at: datetime) -> dict[str, Any]:
        """Persist one newly completed bar and replay; exact duplicate retries are idempotent."""
        candle = self._utc_candle(candle)
        received_at = self._utc_time(observed_at, "observed_at")
        if candle.timestamp.minute % 15 or candle.timestamp.second or candle.timestamp.microsecond:
            raise PaperInputError("15-minute candle timestamp is not aligned to a quarter-hour")
        if candle.timestamp < self.warmup[-1].timestamp + CANDLE_INTERVAL:
            raise PaperInputError("prospective bars must start strictly after the frozen historical dataset")
        close_at = candle.timestamp + CANDLE_INTERVAL
        if received_at < close_at:
            raise PaperInputError("partial candle rejected: candle close time has not elapsed")
        if received_at - close_at > MAX_INGESTION_LATENESS:
            raise PaperInputError("late candle rejected: recover downtime before resuming; do not simulate historical fills")

        with self._single_writer():
            # Refresh under the lock to avoid using a stale in-memory cursor if
            # two local processes were started against the same state directory.
            self.prospective = self._load_journal()
            existing = next((item for item in self.prospective if item[0].timestamp == candle.timestamp), None)
            if existing:
                if existing[0] != candle:
                    raise PaperInputError("conflicting duplicate timestamp rejected")
                return {"status": "duplicate_ignored", "timestamp": candle.timestamp.isoformat(), "replayed": False}
            previous = self.prospective[-1][0].timestamp if self.prospective else self.warmup[-1].timestamp
            if candle.timestamp <= previous:
                raise PaperInputError("out-of-order candle rejected")
            if candle.timestamp != previous + CANDLE_INTERVAL:
                raise PaperInputError("missing candle interval rejected; halt and investigate the feed gap")

            self.prospective.append((candle, received_at))
            self._save_journal()
            if len(self.prospective) < 2:
                return {"status": "accepted_waiting_for_next_bar", "timestamp": candle.timestamp.isoformat(), "replayed": False}
            result = self._replay_and_export()
            return {"status": "paper_replay_updated", "timestamp": candle.timestamp.isoformat(),
                    "replayed": True, "trades_closed": len(result.trades),
                    "marked_equity": result.equity_curve[-1].equity if result.equity_curve else INITIAL_EQUITY,
                    "safety_events": result.kill_events}

    def _save_journal(self) -> None:
        rows = ["timestamp,open,high,low,close,volume,received_at"]
        rows.extend(
            ",".join((c.timestamp.isoformat(), repr(c.open), repr(c.high), repr(c.low), repr(c.close),
                      "" if c.volume is None else repr(c.volume), received.isoformat()))
            for c, received in self.prospective
        )
        self._atomic_text(self.candle_path, "\n".join(rows) + "\n")

    def _replay_and_export(self) -> BacktestResult:
        scored = [candle for candle, _ in self.prospective]
        risk = RiskConfig(risk_per_trade_pct=RISK_PER_TRADE, max_position_notional_pct=1.0,
                          max_margin_fraction=0.8, leverage=LEVERAGE,
                          estimated_round_trip_cost_pct=SIZING_COST_RESERVE)
        config = BacktestConfig(initial_equity=INITIAL_EQUITY, fee_rate=FEE_RATE,
                                slippage_bps=SLIPPAGE_BPS, funding_rate_per_8h=FUNDING_RATE_PER_8H,
                                candle_minutes=15, max_participation_rate=PARTICIPATION_RATE,
                                force_close_at_end=False, logging_level=LogLevel.DEBUG,
                                diagnostic_mode=True, diagnostics_dir=self.state_dir / "_replay_diagnostics",
                                kill_switch_mode=KillSwitchMode.SAFETY)
        engine = IntrabarBacktester(config, PositionSizer(risk), kill_switch=KillSwitch(KillSwitchConfig()))
        result = engine.run(scored, ensemble=StrategyEnsemble(), warmup_candles=self.warmup)
        trade_rows = [normalize_trade(trade, SLIPPAGE_BPS / 10_000) for trade in result.trades]
        assert_trade_reconciliation(trade_rows)
        diagnostic_dir = self.state_dir / "_replay_diagnostics"
        raw_events = [json.loads(line) for line in (diagnostic_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]
        # This is a backtest-end artifact, not a real cancellation. A subsequent
        # deterministic replay may carry the same order to the next observed bar.
        raw_events = [event for event in raw_events if not (
            event.get("event_type") == "ORDER_CANCELLED"
            and event.get("reason") == "backtest ended before remaining quantity filled"
        )]
        for candle, received in self.prospective:
            raw_events.append({"event_type": "PAPER_CANDLE_ACCEPTED",
                               "timestamp": received.isoformat(),
                               "candle_open_timestamp": candle.timestamp.isoformat(),
                               "candle_close_timestamp": (candle.timestamp + CANDLE_INTERVAL).isoformat(),
                               "ohlcv": {"open": candle.open, "high": candle.high, "low": candle.low,
                                         "close": candle.close, "volume": candle.volume}})
        raw_events.sort(key=lambda event: datetime.fromisoformat(str(event["timestamp"])).astimezone(UTC))
        for event in raw_events:
            canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
            event["event_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self._atomic_text(self.state_dir / "paper_events.jsonl", "".join(
            json.dumps(event, sort_keys=True, default=str) + "\n" for event in raw_events
        ))
        self._write_csv(self.state_dir / "paper_trades.csv", trade_rows)
        self._write_csv(self.state_dir / "paper_equity.csv", [
            {"candle_open_timestamp": point.timestamp.isoformat(),
             "decision_at_close_timestamp": (point.timestamp + CANDLE_INTERVAL).isoformat(),
             "equity": point.equity, "drawdown_pct": point.drawdown_pct}
            for point in result.equity_curve
        ])
        summary = {
            "paper_only": True,
            "real_order_submission": False,
            "dataset_sha256": self.dataset_sha256,
            "prospective_candles": len(self.prospective),
            "trades_closed": len(result.trades),
            "realized_final_equity": result.final_equity,
            "last_marked_equity": result.equity_curve[-1].equity if result.equity_curve else INITIAL_EQUITY,
            "net_pnl_closed_trades": sum(trade.net_pnl for trade in result.trades),
            "kill_switch_events": result.kill_events,
            "diagnostics": result.diagnostics,
            "funding_event_count": sum(event.get("event_type") == "FUNDING_CHARGED" for event in raw_events),
            "event_count": len(raw_events),
            "accounting_note": "Open positions are marked in paper_equity.csv and are not force-closed at replay end.",
        }
        self._atomic_json(self.state_dir / "paper_summary.json", summary)
        return result

    def _write_csv(self, path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            self._atomic_text(path, "\n")
            return
        fields = list(dict.fromkeys(key for row in rows for key in row))
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)

    @staticmethod
    def _atomic_text(path: Path, text: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)

    def _atomic_json(self, path: Path, value: dict[str, Any]) -> None:
        self._atomic_text(path, json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
