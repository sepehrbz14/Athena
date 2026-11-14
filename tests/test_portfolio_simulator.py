from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_bot.models import Candle, Side
from crypto_bot.portfolio import PortfolioSimulator
from crypto_bot.portfolio_models import FrozenSignal, PortfolioConfig, SignalDisposition


ROOT = Path(__file__).resolve().parents[1]
CONFIG = PortfolioConfig.from_path(ROOT / "configs/athena2_portfolio_baseline_v1.json")


def make_candles(*, count=6, close=100.0, highs=None, lows=None, volumes=None, gap_at=None):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        timestamp = start + timedelta(minutes=15 * index + (15 if gap_at is not None and index >= gap_at else 0))
        op = close
        high = highs[index] if highs else close + 1
        low = lows[index] if lows else close - 1
        rows.append(Candle(timestamp, op, max(high, op), min(low, op), close, volumes[index] if volumes else 1000.0))
    return rows


def signal(candles, *, signal_id=1, side=Side.LONG, stop=95, target=120, index=0, reference=None):
    candle = candles[index]
    return FrozenSignal(signal_id, "BTCUSDT", candle.timestamp,
        candle.timestamp + timedelta(minutes=15), "breakout", side, Decimal("0.8"),
        Decimal(str(reference if reference is not None else candle.close)), Decimal(str(stop)),
        Decimal(str(target)) if target is not None else None, "a" * 64, "b" * 64, index)


class PortfolioSimulatorTests(unittest.TestCase):
    def test_full_portfolio_replay_matches_versioned_golden_fixture(self):
        candles = make_candles(count=5)
        sig = signal(candles, stop=95, target=130)
        actual = PortfolioSimulator(CONFIG).run(candles, [sig],
            source_manifest={"fixture": "phase4_single_long_replay_v1"}).as_record()
        expected = json.loads((ROOT / "tests/fixtures/portfolio_simulator/phase4_single_long_replay_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)

    def test_long_entry_funding_stop_and_eod_reconcile(self):
        candles = make_candles(count=5, highs=[101, 101, 101, 101, 101], lows=[99, 99, 90, 99, 99])
        sig = signal(candles, stop=95, target=130)
        disposition = SignalDisposition(1, 1, "BTCUSDT", sig.available_at, "breakout", True, "eligible")
        result = PortfolioSimulator(CONFIG).run(candles, [sig], source_manifest={"_dispositions": [disposition]})
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.ledger.positions, {})
        self.assertEqual(len(result.ledger.completed_positions), 1)
        self.assertEqual(result.ledger.reconcile({})["cash_delta"], Decimal("0"))
        funding = [entry for entry in result.ledger.journal if entry.entry_type == "FUNDING"]
        self.assertTrue(funding)
        # Long pays positive legacy funding charge, recorded as negative cash.
        self.assertTrue(all(entry.amount_usdt < 0 for entry in funding))
        self.assertEqual(result.dispositions[0].status, "accepted")
        self.assertTrue(any(e["event_type"] == "POSITION_CLOSED" for e in result.events))

    def test_short_side_funding_receipt_and_exact_journal_replay(self):
        candles = make_candles(count=5)
        sig = signal(candles, side=Side.SHORT, stop=105, target=80)
        disposition = SignalDisposition(1, 1, "BTCUSDT", sig.available_at, "breakout", True, "eligible")
        first = PortfolioSimulator(CONFIG).run(candles, [sig], source_manifest={"_dispositions": [disposition]})
        second = PortfolioSimulator(CONFIG).run(candles, [sig], source_manifest={"_dispositions": [disposition]})
        self.assertEqual(first.as_record(), second.as_record())
        funding = [entry for entry in first.ledger.journal if entry.entry_type == "FUNDING"]
        self.assertTrue(funding)
        self.assertTrue(all(entry.amount_usdt > 0 for entry in funding))

    def test_negative_funding_rate_reverses_ledger_cash_sign(self):
        from dataclasses import replace
        execution = replace(CONFIG.execution, funding_rate_per_8h=-0.0001)
        config = replace(CONFIG, execution=execution)
        candles = make_candles(count=5)
        sig = signal(candles, side=Side.LONG, stop=95, target=130)
        result = PortfolioSimulator(config).run(candles, [sig])
        funding = [entry for entry in result.ledger.journal if entry.entry_type == "FUNDING"]
        self.assertTrue(funding)
        self.assertTrue(all(entry.amount_usdt > 0 for entry in funding))
        result.ledger.reconcile({})

    def test_allocation_priority_and_position_collision_dispositions(self):
        candles = make_candles(count=5)
        first = signal(candles, signal_id=2, side=Side.LONG)
        second = FrozenSignal(3, "BTCUSDT", first.candle_open_time, first.available_at, "momentum", Side.SHORT,
            Decimal("0.8"), first.reference_price, Decimal("105"), Decimal("80"), "a"*64, "b"*64, 0)
        rows = [SignalDisposition(i + 1, s.signal_id, s.instrument_id, s.available_at, s.strategy, True, "eligible")
            for i, s in enumerate((first, second))]
        result = PortfolioSimulator(CONFIG).run(candles, [second, first], source_manifest={"_dispositions": rows})
        reasons = {item.signal_id: item.reason_code for item in result.dispositions}
        self.assertEqual(reasons[2], None)
        self.assertEqual(reasons[3], "CONFLICTING_SIGNAL_BATCH")
        self.assertEqual(len(result.ledger.completed_positions), 1)

    def test_completed_position_identity_collision_is_rejected_and_reconciles(self):
        candles = make_candles(count=7, highs=[101, 103, 101, 101, 101, 101, 101],
            lows=[99, 99, 99, 99, 99, 99, 99], volumes=[10000] * 7)
        first = signal(candles, signal_id=7, stop=95, target=102, index=0)
        second = signal(candles, signal_id=7, stop=95, target=130, index=2)
        result = PortfolioSimulator(CONFIG).run(candles, [first, second])
        self.assertEqual(len(result.ledger.completed_positions), 1)
        self.assertTrue(any(event["event_type"] == "ORDER_REJECTED"
            and event["reason_code"] == "POSITION_ID_COLLISION" for event in result.events))
        self.assertEqual(next(item for item in result.dispositions if item.signal_id == 7 and item.available_at == second.available_at).reason_code,
            "POSITION_ID_COLLISION")
        self.assertEqual(result.ledger.reservations, {})
        result.ledger.reconcile({})

    def test_gap_does_not_consume_pending_eligibility_and_checkpoint_replay_is_equal(self):
        candles = make_candles(count=6, gap_at=1)
        sig = signal(candles, index=0)
        disposition = SignalDisposition(1, 1, "BTCUSDT", sig.available_at, "breakout", True, "eligible")
        with TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint.json"
            first = PortfolioSimulator(CONFIG).run(candles, [sig], source_manifest={"_dispositions": [disposition]},
                checkpoint_path=checkpoint, stop_after_index=2)
            second = PortfolioSimulator(CONFIG).run(candles, [sig], source_manifest={"_dispositions": [disposition]}, resume_from=checkpoint)
            uninterrupted = PortfolioSimulator(CONFIG).run(candles, [sig], source_manifest={"_dispositions": [disposition]})
        self.assertEqual(first.status, "checkpointed")
        self.assertEqual(second.as_record(), uninterrupted.as_record())
        self.assertIn("ORDER_WAITING_FOR_LIQUIDITY", [event["event_type"] for event in first.events])

    def test_end_of_data_force_close_releases_position_and_reconciles(self):
        candles = make_candles(count=4)
        sig = signal(candles)
        result = PortfolioSimulator(CONFIG).run(candles, [sig])
        self.assertFalse(result.ledger.positions)
        self.assertEqual(result.ledger.reservations, {})
        self.assertEqual(result.reconciliation["equity_delta"], Decimal("0"))

    def test_partial_fill_expires_and_reservation_never_posts_cash(self):
        candles = make_candles(count=5, volumes=[20, 20, 20, 20, 20])
        sig = signal(candles)
        result = PortfolioSimulator(CONFIG).run(candles, [sig])
        self.assertTrue(any(row["event"] == "fill" and row["remaining"] > 0 for row in result.orders))
        self.assertTrue(any(row["event"] == "cancelled" and row["reason"] == "ORDER_EXPIRED" for row in result.orders))
        self.assertFalse(any(entry.entry_type in {"RESERVATION", "MARGIN_RESERVATION", "RESERVATION_RELEASE"}
            for entry in result.ledger.journal))
        self.assertEqual(result.ledger.reservations, {})
        result.ledger.reconcile({})

    def test_actual_next_open_slippage_reduces_risk_sized_quantity(self):
        candles = make_candles(count=5)
        candles[1] = Candle(candles[1].timestamp, 110, 111, 109, 110, 1000)
        sig = signal(candles, stop=95, target=130)
        result = PortfolioSimulator(CONFIG).run(candles, [sig])
        requested = next(row["quantity"] for row in result.orders if row["event"] == "reserved")
        filled = next(row["quantity"] for row in result.orders if row["event"] == "fill")
        self.assertLess(filled, requested)
        self.assertGreaterEqual(filled, CONFIG.minimum_quantity_btc)

    def test_sizing_compounds_from_current_marked_equity_after_a_winning_trade(self):
        candles = make_candles(count=7, highs=[101, 112, 101, 101, 101, 101, 101],
            lows=[99, 99, 99, 99, 99, 99, 99])
        first = signal(candles, signal_id=1, stop=95, target=110, index=0)
        second = signal(candles, signal_id=2, stop=95, target=130, index=2)
        result = PortfolioSimulator(CONFIG).run(candles, [first, second])
        reserved = {row["order_id"]: row["quantity"] for row in result.orders if row["event"] == "reserved"}
        self.assertGreater(reserved[second.order_id], reserved[first.order_id])

    def test_stop_target_collision_is_resolved_by_shared_execution_rule(self):
        candles = make_candles(count=5, highs=[101, 103, 101, 101, 101], lows=[99, 97, 99, 99, 99])
        sig = signal(candles, stop=98, target=102)
        result = PortfolioSimulator(CONFIG).run(candles, [sig])
        closure = next(event for event in result.events if event["event_type"] == "POSITION_CLOSED")
        self.assertEqual(closure["payload"]["reason"], "stop_loss")
        self.assertEqual(closure["payload"]["raw_price"], Decimal("98"))

    def test_open_gap_stop_closes_before_any_pending_allocation(self):
        candles = make_candles(count=5)
        candles[2] = Candle(candles[2].timestamp, 90, 92, 88, 91, 1000)
        sig = signal(candles, stop=95, target=130)
        result = PortfolioSimulator(CONFIG).run(candles, [sig])
        closure = next(event for event in result.events if event["event_type"] == "POSITION_CLOSED")
        self.assertEqual(closure["phase"], "bar_open_exit")
        self.assertEqual(closure["payload"]["raw_price"], Decimal("90"))
        result.ledger.reconcile({})

    def test_funding_uses_full_elapsed_interval_across_data_gap(self):
        # Position opens on candle 1, then a 30-minute interval separates the
        # next candle. Carried quantity must accrue all elapsed time once.
        candles = make_candles(count=5)
        candles[3] = Candle(candles[2].timestamp + timedelta(minutes=30), 100, 101, 99, 100, 1000)
        candles[4] = Candle(candles[3].timestamp + timedelta(minutes=15), 100, 101, 99, 100, 1000)
        sig = signal(candles, stop=95, target=130)
        result = PortfolioSimulator(CONFIG).run(candles, [sig])
        quantity = result.ledger.completed_positions[0].quantity_closed
        expected_charge = sum((quantity * Decimal("100") * Decimal(str(CONFIG.execution.funding_rate_per_8h))
            * Decimal(str(hours)) / Decimal("8") for hours in (0.25, 0.25, 0.5, 0.25)), Decimal("0"))
        posted = sum((entry.amount_usdt for entry in result.ledger.journal if entry.entry_type == "FUNDING"), Decimal("0"))
        self.assertLessEqual(abs(posted + expected_charge), Decimal("1e-18"))
        result.ledger.reconcile({})

    def test_minimum_equity_is_entry_lock_not_existing_position_liquidation(self):
        # A deliberately high test floor makes the distinction observable
        # without changing the frozen baseline configuration.
        config = replace(CONFIG, minimum_equity_usdt=Decimal("9999"))
        candles = make_candles(count=5, highs=[101, 101, 101, 101, 101],
            lows=[99, 99, 99, 99, 99])
        candles[2] = Candle(candles[2].timestamp, 99.5, 100, 99, 99.5, 1000)
        sig = signal(candles, stop=95, target=130)
        result = PortfolioSimulator(config).run(candles, [sig])
        closures = [event for event in result.events if event["event_type"] == "POSITION_CLOSED"]
        self.assertEqual(len(closures), 1)
        self.assertEqual(closures[0]["payload"]["reason"], "end_of_data")
        self.assertEqual(result.status, "minimum_equity_locked")
        result.ledger.reconcile({})

    def test_adverse_gap_below_minimum_equity_locks_future_signals_and_reports_remaining_cash(self):
        candles = make_candles(count=4, highs=[101, 101, 49, 49], lows=[100.01, 100.01, 47.441, 47.441],
            volumes=[10000] * 4)
        candles[2] = Candle(candles[2].timestamp, 47.441, 48, 47.441, 47.441, 10000)
        first = signal(candles, signal_id=1, stop=99.99, target=None)
        later = signal(candles, signal_id=2, index=2, stop=40, target=60)
        result = PortfolioSimulator(CONFIG).run(candles, [first, later])
        self.assertEqual(result.status, "minimum_equity_locked")
        self.assertGreater(result.ledger.cash, Decimal("0"))
        self.assertLess(result.ledger.cash, CONFIG.minimum_equity_usdt)
        second_disposition = next(d for d in result.dispositions if d.signal_id == 2)
        self.assertEqual(second_disposition.status, "rejected")
        self.assertEqual(second_disposition.reason_code, "MINIMUM_EQUITY")
        closure = next(event for event in result.events if event["event_type"] == "POSITION_CLOSED")
        self.assertEqual(closure["payload"]["reason"], "stop_loss")
        self.assertTrue(closure["payload"]["gap_at_open"])
        self.assertFalse(any(event["event_type"] == "INSOLVENCY_DECLARED" for event in result.events))
        summary = result.financial_summary()
        self.assertEqual(Decimal(summary["insolvency_deficit_usdt"]), Decimal("0"))
        self.assertEqual(Decimal(summary["final_equity_usdt"]), result.ledger.cash)
        self.assertGreater(Decimal(summary["net_loss_from_initial_equity_usdt"]), Decimal("9999"))
        result.ledger.reconcile({})

    def test_catastrophic_gap_triggers_insolvency_lock_and_worst_price_exit(self):
        candles = make_candles(count=4, highs=[101, 101, .002, .002], lows=[99, 100, .0005, .0005],
            volumes=[10000, 10000, 10000, 10000])
        candles[2] = Candle(candles[2].timestamp, .001, .002, .0005, .001, 1000)
        candles[3] = Candle(candles[3].timestamp, .001, .002, .0005, .001, 1000)
        sig = signal(candles, stop=99.99, target=None)
        result = PortfolioSimulator(CONFIG).run(candles, [sig])
        self.assertEqual(result.status, "insolvent")
        self.assertTrue(any(event["event_type"] == "INSOLVENCY_DECLARED" for event in result.events))
        self.assertFalse(result.ledger.positions)
        self.assertLess(result.ledger.cash, 0)
        summary = result.financial_summary()
        self.assertEqual(Decimal(summary["insolvency_deficit_usdt"]), -result.ledger.cash)
        self.assertTrue(summary["insolvent"])
        closure = next(event for event in result.events if event["event_type"] == "POSITION_CLOSED"
            and event["payload"].get("reason") == "insolvency")
        self.assertEqual(closure["payload"]["raw_price"], Decimal("0.0005"))
        result.ledger.reconcile({})

    def test_single_position_cash_result_matches_canonical_legacy_trade_accounting(self):
        candles = make_candles(count=5)
        sig = signal(candles, side=Side.SHORT, stop=105, target=80)
        result = PortfolioSimulator(CONFIG).run(candles, [sig])
        completed = result.ledger.completed_positions[0]
        entry_event = next(event for event in result.events if event["event_type"] in {"ORDER_FILLED", "ORDER_PARTIALLY_FILLED"})
        entry = entry_event["payload"]
        from crypto_bot.models import Position
        legacy_position = Position(Side.SHORT, float(completed.quantity_closed), float(entry["price"]),
            float(sig.stop_price), float(sig.take_profit), candles[1].timestamp, sig.strategy,
            float(completed.entry_fees_paid), float(-completed.funding_paid), signal_id=sig.signal_id)
        legacy_trade = CONFIG.execution.close_position(legacy_position,
            candles[-1].timestamp + timedelta(minutes=15), candles[-1].close, "end_of_data")
        self.assertAlmostEqual(float(completed.realized_gross_pnl), legacy_trade.gross_pnl, places=10)
        self.assertAlmostEqual(float(completed.final_net_pnl), legacy_trade.net_pnl, places=10)
        self.assertAlmostEqual(float(completed.funding_paid), -legacy_trade.funding_paid, places=10)

    def test_single_position_parity_matches_legacy_backtester_trade_and_equity(self):
        from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
        from crypto_bot.risk import PositionSizer, RiskConfig
        from crypto_bot.models import Signal
        from crypto_bot.strategies import EnsembleDecision

        candles = make_candles(count=6, volumes=[10000] * 6)
        sig = signal(candles, side=Side.LONG, stop=95, target=130)
        entry = CONFIG.execution.slipped_price(float(sig.reference_price), Side.LONG)
        stop_exit = CONFIG.execution.slipped_price(float(sig.stop_price), Side.SHORT)
        portfolio_unit_risk = abs(entry - stop_exit) + CONFIG.execution.fee_rate * (abs(entry) + abs(stop_exit))
        legacy_cost_pct = (portfolio_unit_risk - abs(entry - float(sig.stop_price))) / entry
        sizer = PositionSizer(RiskConfig(risk_per_trade_pct=float(CONFIG.per_trade_risk_pct),
            max_position_notional_pct=2, max_margin_fraction=1, leverage=float(CONFIG.leverage),
            estimated_round_trip_cost_pct=legacy_cost_pct, min_quantity=float(CONFIG.minimum_quantity_btc)))

        class OneDecision:
            used = False
            def decide(self, market):
                if self.used:
                    return EnsembleDecision(None, (), ())
                self.used = True
                return EnsembleDecision(Signal(Side.LONG, float(sig.stop_price), float(sig.take_profit),
                    float(sig.confidence), sig.strategy, "frozen parity fixture"), (sig.strategy,), ())

        legacy = IntrabarBacktester(BacktestConfig(initial_equity=float(CONFIG.initial_equity_usdt),
            fee_rate=CONFIG.execution.fee_rate, slippage_bps=CONFIG.execution.slippage_bps,
            funding_rate_per_8h=CONFIG.execution.funding_rate_per_8h,
            candle_minutes=CONFIG.execution.candle_minutes,
            max_participation_rate=CONFIG.execution.max_participation_rate,
            force_close_at_end=True), sizer=sizer).run(candles, ensemble=OneDecision())
        portfolio = PortfolioSimulator(CONFIG).run(candles, [sig])
        legacy_trade = legacy.trades[0]
        completed = portfolio.ledger.completed_positions[0]
        self.assertAlmostEqual(float(completed.quantity_closed), legacy_trade.quantity, places=9)
        self.assertAlmostEqual(float(completed.realized_gross_pnl), legacy_trade.gross_pnl, places=8)
        self.assertAlmostEqual(float(completed.entry_fees_paid), legacy_trade.entry_fee, places=8)
        self.assertAlmostEqual(float(completed.exit_fees_paid), legacy_trade.exit_fee, places=8)
        self.assertAlmostEqual(float(completed.funding_paid), -legacy_trade.funding_paid, places=8)
        self.assertAlmostEqual(float(completed.final_net_pnl), legacy_trade.net_pnl, places=8)
        self.assertAlmostEqual(float(portfolio.ledger.cash), legacy.final_equity, places=8)


if __name__ == "__main__":
    unittest.main()
