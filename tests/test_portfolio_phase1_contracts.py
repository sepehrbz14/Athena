"""Frozen Phase 1 contract and legacy-engine parity fixtures."""
from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.diagnostics import LogLevel
from crypto_bot.models import Candle, Side, Signal
from crypto_bot.risk import PositionSizer, RiskConfig
from crypto_bot.strategies import EnsembleDecision


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "portfolio_parity" / "legacy_stop_gap_funding_v1.json"
SCHEMAS = ROOT / "schemas"


class OneSignal:
    def __init__(self, signal: Signal) -> None:
        self.signal = signal
        self.used = False

    def decide(self, _market):
        if self.used:
            return EnsembleDecision(None, (), ())
        self.used = True
        return EnsembleDecision(self.signal, (self.signal.strategy,), ())


class PortfolioPhase1ContractTests(unittest.TestCase):
    def test_frozen_contract_schemas_have_versioned_closed_vocabularies(self):
        event = json.loads((SCHEMAS / "athena2_portfolio_event_v1.schema.json").read_text(encoding="utf-8"))
        cash = json.loads((SCHEMAS / "athena2_cash_journal_entry_v1.schema.json").read_text(encoding="utf-8"))
        signal = json.loads((SCHEMAS / "athena2_frozen_signal_v1.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(event["properties"]["schema_version"]["const"], "athena.portfolio.event.v1")
        self.assertEqual(event["additionalProperties"], False)
        self.assertIn("ACCOUNT_MARKED", event["properties"]["event_type"]["enum"])
        self.assertIn("FORCED_LIQUIDATION", event["properties"]["event_type"]["enum"])
        self.assertEqual(cash["properties"]["asset"]["const"], "USDT")
        self.assertNotIn("MARGIN_RESERVED", cash["properties"]["entry_type"]["enum"])
        self.assertEqual(signal["properties"]["instrument_id"]["const"], "BTCUSDT")
        self.assertNotIn("forward_outcomes", signal["properties"])

    def test_current_backtester_reproduces_frozen_stop_gap_funding_golden(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        config_values = dict(fixture["config"])
        risk_values = {key: config_values.pop(key) for key in (
            "risk_per_trade_pct", "max_position_notional_pct", "max_margin_fraction",
            "leverage", "estimated_round_trip_cost_pct",
        )}
        signal_data = fixture["signal"]
        signal = Signal(
            side=Side(signal_data["side"]),
            stop_price=signal_data["stop_price"],
            take_profit=signal_data["take_profit"],
            confidence=signal_data["confidence"],
            strategy=signal_data["strategy"],
            rationale=signal_data["rationale"],
        )
        candles = [
            Candle(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], volume=row["volume"],
            )
            for row in fixture["candles"]
        ]
        expected = fixture["expected"]

        with TemporaryDirectory() as first_dir, TemporaryDirectory() as second_dir:
            runs = []
            for output in (first_dir, second_dir):
                config = BacktestConfig(
                    **config_values,
                    diagnostic_mode=True,
                    diagnostics_dir=output,
                    logging_level=LogLevel.DEBUG,
                )
                sizer = PositionSizer(RiskConfig(**risk_values))
                result = IntrabarBacktester(config, sizer).run(candles, OneSignal(signal))
                runs.append((result, (Path(output) / "events.jsonl").read_bytes()))

        result, event_bytes = runs[0]
        self.assertEqual(event_bytes, runs[1][1], "identical input/config must produce byte-identical events")
        self.assertEqual(result.initial_equity, expected["initial_equity"])
        self.assertAlmostEqual(result.final_equity, expected["final_equity"], delta=1e-9)
        self.assertEqual(len(result.trades), 1)
        trade = asdict(result.trades[0])
        trade["side"] = trade["side"].value
        trade["entry_time"] = trade["entry_time"].isoformat()
        trade["exit_time"] = trade["exit_time"].isoformat()
        for key, value in expected["trade"].items():
            if isinstance(value, float):
                self.assertAlmostEqual(trade[key], value, delta=1e-9, msg=key)
            else:
                self.assertEqual(trade[key], value, key)

        self.assertEqual(len(result.equity_curve), len(expected["equity_curve"]))
        for actual, wanted in zip(result.equity_curve, expected["equity_curve"]):
            self.assertEqual(actual.timestamp.isoformat(), wanted["timestamp"])
            self.assertAlmostEqual(actual.equity, wanted["equity"], delta=1e-9)
            self.assertAlmostEqual(actual.drawdown_pct, wanted["drawdown_pct"], delta=1e-12)

        events = [json.loads(line) for line in event_bytes.decode("utf-8").splitlines()]
        self.assertEqual([event["event_type"] for event in events], expected["event_types"])
        actual_details = [
            {key: event[key] for key in wanted if key in event}
            for event, wanted in zip(events, expected["event_details"])
        ]
        self.assertEqual(actual_details, expected["event_details"])


if __name__ == "__main__":
    unittest.main()
