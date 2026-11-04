import unittest
import csv
import io
import json
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester, _IncrementalMarketData
from crypto_bot.controls import KillSwitch, KillSwitchConfig, KillSwitchMode
from crypto_bot.diagnostics import LogLevel, PerformanceTimings
from crypto_bot.io import load_ohlcv_csv
from crypto_bot.models import Candle, Side, Signal
from crypto_bot.risk import PositionSizer, RiskConfig
from crypto_bot.strategies import EnsembleDecision
from crypto_bot.validation import monte_carlo_trade_order


class OneSignalEnsemble:
    def __init__(self, signal: Signal) -> None:
        self.signal = signal
        self.used = False

    def decide(self, market):  # noqa: ANN001 - deliberately matches ensemble protocol
        if self.used:
            return EnsembleDecision(None, (), ())
        self.used = True
        return EnsembleDecision(self.signal, ("test",), ())


class DecisionTraceEnsemble:
    """Capture every strategy decision while preserving the real ensemble."""

    def __init__(self) -> None:
        from crypto_bot.strategies import StrategyEnsemble
        self.delegate = StrategyEnsemble()
        self.decisions = []

    def decide(self, market):  # noqa: ANN001 - matches the strategy ensemble protocol
        decision = self.delegate.decide(market)
        self.decisions.append((market.candles_15m[-1].timestamp, decision))
        return decision


class SnapshotTraceEnsemble:
    """Capture only information visible to each close-time decision."""

    def __init__(self) -> None:
        self.snapshots = []

    def decide(self, market):  # noqa: ANN001 - matches the strategy ensemble protocol
        self.snapshots.append((
            market.candles_15m[-1].timestamp,
            tuple(market.candles_15m),
            tuple(market.candles_1h),
            tuple(market.candles_4h),
            market.regime,
            market.features,
        ))
        return EnsembleDecision(None, (), ())


def candle(minutes: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        open=open_, high=high, low=low, close=close, volume=100,
    )


class IntrabarBacktesterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sizer = PositionSizer(RiskConfig(
            risk_per_trade_pct=0.005,
            max_position_notional_pct=2,
            max_margin_fraction=1,
            estimated_round_trip_cost_pct=0,
        ))
        self.config = BacktestConfig(fee_rate=0, slippage_bps=0, funding_rate_per_8h=0, max_participation_rate=1)

    def test_signal_fills_next_open_and_costs_are_netted(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        result = IntrabarBacktester(self.config, self.sizer).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)],
            OneSignalEnsemble(signal),
        )

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_time, candle(15, 100, 106, 99, 105).timestamp)
        self.assertEqual(trade.exit_reason, "take_profit")
        self.assertAlmostEqual(trade.quantity, 10)
        self.assertAlmostEqual(trade.net_pnl, 50)
        self.assertAlmostEqual(result.final_equity, 10_050)

    def test_incremental_higher_timeframe_bars_contain_only_observed_15m_candles(self) -> None:
        """Hand-checkable 1H/4H partial OHLCV snapshots at each close."""
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        bars = [
            Candle(start + timedelta(minutes=15 * i), 100 + i, 102 + i, 99 + i, 101 + i, 10 + i)
            for i in range(5)
        ]
        market = _IncrementalMarketData()

        for bar in bars[:2]:
            market.update(bar)
            market.refresh_1h_features()
            market.refresh_4h_features(IntrabarBacktester(self.config, self.sizer).regime_classifier)
            market.refresh_15m_features()
        self.assertEqual(market.candles_1h[-1], Candle(start, 100, 103, 99, 102, 21))
        self.assertEqual(market.candles_4h[-1], Candle(start, 100, 103, 99, 102, 21))

        # The third candle changes the in-progress high/low/close/volume; the
        # next hour starts a new 1H candle while remaining in the same 4H bar.
        market.update(bars[2])
        self.assertEqual(market.candles_1h[-1], Candle(start, 100, 104, 99, 103, 33))
        self.assertEqual(market.candles_4h[-1], Candle(start, 100, 104, 99, 103, 33))
        market.update(bars[4])  # deliberately skip bar 3 only in this isolated bucket test
        self.assertEqual(market.candles_1h[-1].timestamp, start + timedelta(hours=1))
        self.assertEqual(market.candles_1h[-1], Candle(start + timedelta(hours=1), 104, 106, 103, 105, 14))
        self.assertEqual(market.candles_4h[-1].close, 105)
        self.assertEqual(market.candles_4h[-1].volume, 47)

    def test_window_warmup_seeds_features_but_not_account_or_result_curve(self) -> None:
        warmup = [candle(15 * i, 100 + i * 0.01, 101 + i * 0.01, 99 + i * 0.01, 100.5 + i * 0.01)
                  for i in range(30)]
        scored = [candle(15 * i, 101 + i * 0.01, 102 + i * 0.01, 100 + i * 0.01, 101.5 + i * 0.01)
                  for i in range(30, 32)]
        trace = SnapshotTraceEnsemble()

        result = IntrabarBacktester(self.config, self.sizer).run(
            scored, trace, warmup_candles=warmup,
        )

        self.assertEqual(len(trace.snapshots), 1)
        decision_time, seen_15m, *_ = trace.snapshots[0]
        self.assertEqual(decision_time, scored[0].timestamp)
        self.assertEqual(len(seen_15m), len(warmup) + 1)
        self.assertEqual(seen_15m[0], warmup[0])
        self.assertEqual(seen_15m[-1], scored[0])
        self.assertEqual(result.diagnostics["candles_processed"], len(scored))
        self.assertEqual([point.timestamp for point in result.equity_curve], [c.timestamp for c in scored])
        self.assertEqual(result.initial_equity, result.final_equity)
        self.assertEqual(result.trades, [])
        self.assertEqual(result.kill_events, [])

    def test_warmup_must_precede_scored_window(self) -> None:
        scored = [candle(15 * i, 100, 101, 99, 100) for i in range(2)]
        with self.assertRaisesRegex(ValueError, "must precede"):
            IntrabarBacktester(self.config, self.sizer).run(
                scored, warmup_candles=[scored[0]],
            )

    def test_warmup_seeds_atr_safety_check_without_observing_warmup(self) -> None:
        warmup = [candle(15 * i, 100, 101, 99, 100) for i in range(14)]
        scored = [candle(15 * i, 100, 101, 99, 100) for i in range(14, 16)]
        switch = KillSwitch(KillSwitchConfig(max_atr_pct=0.01))

        result = IntrabarBacktester(self.config, self.sizer, kill_switch=switch).run(
            scored, SnapshotTraceEnsemble(), warmup_candles=warmup,
        )

        self.assertEqual(len(result.kill_events), 1)
        timestamp, reason, _ = result.kill_events[0]
        self.assertEqual(timestamp, scored[0].timestamp)
        self.assertEqual(getattr(reason, "value", reason), "unexpected_volatility")

    def test_future_candle_changes_cannot_change_earlier_decision_inputs(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        candles = [
            Candle(start + timedelta(minutes=15 * i), 100 + i, 102 + i, 99 + i, 101 + i, 10 + i)
            for i in range(12)
        ]
        changed_future = list(candles)
        for index in range(7, len(changed_future)):
            old = changed_future[index]
            changed_future[index] = Candle(
                old.timestamp, old.open * 3, old.high * 3, old.low * 3, old.close * 3, (old.volume or 0) * 7,
            )
        prefix_trace, changed_trace = SnapshotTraceEnsemble(), SnapshotTraceEnsemble()
        IntrabarBacktester(self.config, self.sizer).run(candles, prefix_trace)
        IntrabarBacktester(self.config, self.sizer).run(changed_future, changed_trace)
        # Decisions at indexes 0..6 must be byte-for-byte equivalent even
        # though the later part of the dataset is radically different.
        self.assertEqual(prefix_trace.snapshots[:7], changed_trace.snapshots[:7])
        self.assertEqual(len(prefix_trace.snapshots), len(candles) - 1)

    def test_bundled_dataset_archive_confirms_timestamps_are_candle_open_times(self) -> None:
        archive_path = Path(__file__).parent / "BTCUSDT-15m-2020-01-01.zip"
        combined_path = Path(__file__).parent / "tests" / "combined.csv"
        with zipfile.ZipFile(archive_path) as archive:
            source_name = archive.namelist()[0]
            source_text = io.TextIOWrapper(archive.open(source_name), encoding="utf-8")
            source_row = next(csv.reader(source_text))
        open_ms, source_ohlcv, close_ms = int(source_row[0]), source_row[1:6], int(source_row[6])
        self.assertEqual(close_ms - open_ms, 15 * 60 * 1000 - 1)

        with combined_path.open(newline="", encoding="utf-8") as handle:
            rows = csv.DictReader(handle)
            combined_row = next(row for row in rows if int(float(row["timestamp"])) == open_ms)
        self.assertEqual(
            [combined_row[key] for key in ("open", "high", "low", "close", "volume")],
            source_ohlcv,
        )

    def test_future_execution_candle_volume_does_not_change_next_open_fill_size(self) -> None:
        """A next-open fill uses only the prior completed candle's volume."""
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        base = [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)]
        high_volume = [base[0], Candle(base[1].timestamp, 100, 106, 99, 105, 200)]
        low_volume = [base[0], Candle(base[1].timestamp, 100, 106, 99, 105, 100)]
        participation_limited = replace(self.config, max_participation_rate=0.05)
        low_fill = IntrabarBacktester(participation_limited, self.sizer).run(low_volume, OneSignalEnsemble(signal))
        high_fill = IntrabarBacktester(participation_limited, self.sizer).run(high_volume, OneSignalEnsemble(signal))
        self.assertEqual(low_fill.trades[0].quantity, high_fill.trades[0].quantity)
        self.assertAlmostEqual(low_fill.trades[0].quantity, 5)

    def test_prior_volume_can_support_a_full_fill_even_if_current_volume_is_zero(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        candles = [
            Candle(candle(0, 100, 101, 99, 100).timestamp, 100, 101, 99, 100, 1_000),
            Candle(candle(15, 100, 106, 99, 105).timestamp, 100, 106, 99, 105, 0),
        ]
        with TemporaryDirectory() as directory:
            config = replace(self.config, max_participation_rate=0.05, diagnostic_mode=True, diagnostics_dir=directory)
            result = IntrabarBacktester(config, self.sizer).run(candles, OneSignalEnsemble(signal))
            events = [json.loads(line) for line in (Path(directory) / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(result.trades), 1)
        self.assertAlmostEqual(result.trades[0].quantity, 10)
        self.assertEqual(result.partial_fills, 0)
        self.assertTrue(any(event["event_type"] == "ORDER_FILLED" for event in events))

    def test_partial_fill_carries_remainder_and_aggregates_same_trade(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        candles = [
            Candle(start, 100, 101, 99, 100, 100),
            Candle(start + timedelta(minutes=15), 100, 103, 99, 101, 100),
            Candle(start + timedelta(minutes=30), 102, 104, 101, 102, 100),
        ]
        with TemporaryDirectory() as directory:
            config = replace(self.config, max_participation_rate=0.05, diagnostic_mode=True, diagnostics_dir=directory)
            result = IntrabarBacktester(config, self.sizer).run(candles, OneSignalEnsemble(signal))
            events = [json.loads(line) for line in (Path(directory) / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_time, candles[1].timestamp)
        self.assertAlmostEqual(trade.quantity, 10)
        self.assertAlmostEqual(trade.entry_price, 101)
        self.assertAlmostEqual(trade.gross_pnl, 10)
        self.assertEqual(result.partial_fills, 1)
        partial = next(event for event in events if event["event_type"] == "PARTIAL_FILL")
        filled = next(event for event in events if event["event_type"] == "ORDER_FILLED")
        self.assertEqual((partial["order_id"], partial["trade_id"]), (filled["order_id"], filled["trade_id"]))
        self.assertAlmostEqual(partial["remaining_quantity"], filled["requested_size"] - partial["filled_size"])

    def test_zero_prior_volume_waits_then_fills_from_newly_available_prior_volume(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        candles = [
            Candle(start, 100, 101, 99, 100, 0),
            Candle(start + timedelta(minutes=15), 100, 101, 99, 100, 100),
            Candle(start + timedelta(minutes=30), 100, 103, 99, 100, 200),
            Candle(start + timedelta(minutes=45), 100, 106, 99, 105, 200),
        ]
        config = replace(self.config, max_participation_rate=0.05)
        result = IntrabarBacktester(config, self.sizer).run(candles, OneSignalEnsemble(signal))
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_time, candles[2].timestamp)
        self.assertAlmostEqual(result.trades[0].quantity, 10)
        self.assertEqual(result.partial_fills, 1)

    def test_current_partial_bar_ema_matches_batch_ema_without_compounding_updates(self) -> None:
        from crypto_bot.backtest import _StreamingEMA
        from crypto_bot.indicators import ema

        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        bars = [Candle(start + timedelta(hours=i), 100, 110, 99, 100 + i, 1) for i in range(8)]
        live = []
        updater = _StreamingEMA(3)
        for historic in bars[:6]:
            live.append(historic)
            self.assertEqual(updater.update(live), ema([item.close for item in live], 3))
        live.append(bars[6])
        self.assertEqual(updater.update(live), ema([item.close for item in live], 3))
        for replacement in (1000, 1075, 1125):
            live[-1] = Candle(live[-1].timestamp, 100, replacement, 99, replacement, 1)
            self.assertEqual(updater.update(live), ema([item.close for item in live], 3))

    def test_stop_wins_when_one_ohlc_bar_hits_both_stop_and_target(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        result = IntrabarBacktester(self.config, self.sizer).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 94, 100)],
            OneSignalEnsemble(signal),
        )

        self.assertEqual(result.trades[0].exit_reason, "stop_loss")
        self.assertAlmostEqual(result.trades[0].net_pnl, -50)

    def test_long_stop_gap_executes_at_open_below_stop(self) -> None:
        from crypto_bot.models import Position

        position = Position(Side.LONG, 1, 100, 95, 110, candle(0, 100, 101, 99, 100).timestamp, "test", 0)
        gap_bar = candle(15, 90, 96, 88, 93)
        self.assertEqual(IntrabarBacktester(self.config, self.sizer)._intrabar_exit(position, gap_bar), (90, "stop_loss"))

    def test_short_stop_gap_executes_at_open_above_stop(self) -> None:
        from crypto_bot.models import Position

        position = Position(Side.SHORT, 1, 100, 105, 90, candle(0, 100, 101, 99, 100).timestamp, "test", 0)
        gap_bar = candle(15, 110, 112, 104, 107)
        self.assertEqual(IntrabarBacktester(self.config, self.sizer)._intrabar_exit(position, gap_bar), (110, "stop_loss"))

    def test_normal_stop_hits_execute_at_stop_price(self) -> None:
        from crypto_bot.models import Position

        stamp = candle(0, 100, 101, 99, 100).timestamp
        long_position = Position(Side.LONG, 1, 100, 95, 110, stamp, "test", 0)
        short_position = Position(Side.SHORT, 1, 100, 105, 90, stamp, "test", 0)
        self.assertEqual(IntrabarBacktester(self.config, self.sizer)._intrabar_exit(long_position, candle(15, 98, 100, 94, 97)), (95, "stop_loss"))
        self.assertEqual(IntrabarBacktester(self.config, self.sizer)._intrabar_exit(short_position, candle(15, 102, 106, 100, 103)), (105, "stop_loss"))

    def test_short_stop_wins_stop_target_collision(self) -> None:
        from crypto_bot.models import Position

        position = Position(Side.SHORT, 1, 100, 105, 95, candle(0, 100, 101, 99, 100).timestamp, "test", 0)
        self.assertEqual(IntrabarBacktester(self.config, self.sizer)._intrabar_exit(position, candle(15, 100, 106, 94, 100)), (105, "stop_loss"))

    def test_volume_cap_records_partial_fill(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        limited = BacktestConfig(fee_rate=0, slippage_bps=0, funding_rate_per_8h=0, max_participation_rate=0.05)
        result = IntrabarBacktester(limited, self.sizer).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)],
            OneSignalEnsemble(signal),
        )

        self.assertEqual(result.partial_fills, 1)
        self.assertAlmostEqual(result.trades[0].quantity, 5)

    def test_net_pnl_includes_fees_and_prorated_funding(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        costs = BacktestConfig(
            fee_rate=0.001,
            slippage_bps=0,
            funding_rate_per_8h=0.0032,
            max_participation_rate=1,
        )
        result = IntrabarBacktester(costs, self.sizer).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)],
            OneSignalEnsemble(signal),
        )

        trade = result.trades[0]
        self.assertAlmostEqual(trade.gross_pnl, 50)
        self.assertAlmostEqual(trade.entry_fee, 1)
        self.assertAlmostEqual(trade.exit_fee, 1.05)
        self.assertAlmostEqual(trade.funding_paid, 0.105)
        self.assertAlmostEqual(trade.net_pnl, 47.845)

    def test_accepted_entry_has_complete_diagnostic_lifecycle(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        result = IntrabarBacktester(self.config, self.sizer).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)], OneSignalEnsemble(signal)
        )
        summary = result.diagnostics
        self.assertEqual(summary["entries_accepted"], 1)
        # The persisted event stream contains all lifecycle events at INFO for accepted trades.
        self.assertEqual(result.trades[0].trade_id, 1)
        self.assertEqual(result.trades[0].signal_id, 1)
        self.assertEqual(result.trades[0].order_id, 1)

    def test_every_rejection_has_one_typed_reason_and_counter_matches(self) -> None:
        signal = Signal(Side.LONG, 105, 110, 1, "test", "invalid after gap")
        result = IntrabarBacktester(self.config, self.sizer).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 101, 99, 100)], OneSignalEnsemble(signal)
        )
        self.assertEqual(len(result.rejected_orders), 1)
        self.assertEqual(result.rejected_orders[0].reason.value, "INVALID_SIGNAL")
        self.assertEqual(sum(result.diagnostics["rejection_reasons"].values()), len(result.rejected_orders))

    def test_safety_kill_switch_activation_and_skipped_signal_are_diagnosed(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        kill = KillSwitch(KillSwitchConfig(max_consecutive_exchange_errors=1))
        kill.report_exchange_error(candle(0, 100, 101, 99, 100).timestamp, "test halt")
        result = IntrabarBacktester(self.config, self.sizer, kill_switch=kill).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)], OneSignalEnsemble(signal)
        )
        self.assertEqual(result.diagnostics["kill_switch_activations"], 1)
        self.assertEqual(result.diagnostics["entries_skipped_kill_switch"], 1)
        self.assertEqual(result.diagnostics["entries_rejected"], 0)

    def test_research_mode_observes_kill_switch_but_allows_entry(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        kill = KillSwitch(KillSwitchConfig(max_consecutive_exchange_errors=1))
        kill.report_exchange_error(candle(0, 100, 101, 99, 100).timestamp, "test halt")
        result = IntrabarBacktester(
            replace(self.config, kill_switch_mode=KillSwitchMode.OBSERVATIONAL), self.sizer, kill_switch=kill
        ).run([candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)], OneSignalEnsemble(signal))

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.diagnostics["entries_accepted"], 1)
        self.assertEqual(result.diagnostics["entries_skipped_kill_switch"], 0)
        self.assertEqual(result.diagnostics["kill_switch_would_trigger"], 1)

    def test_strategy_performance_is_derived_from_closed_trades(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        result = IntrabarBacktester(self.config, self.sizer).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)], OneSignalEnsemble(signal)
        )

        values = result.diagnostics["strategy_performance"]["test"]
        self.assertEqual(values["signals"], 1)
        self.assertEqual(values["entries"], 1)
        self.assertEqual(values["wins"], 1)
        self.assertEqual(values["losses"], 0)
        self.assertAlmostEqual(values["net_pnl"], 50)

    def test_logging_does_not_change_results_and_diagnostic_mode_exports(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        candles = [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)]
        off = IntrabarBacktester(replace(self.config, logging_level=LogLevel.OFF), self.sizer).run(candles, OneSignalEnsemble(signal))
        with TemporaryDirectory() as directory:
            detailed_config = replace(self.config, diagnostic_mode=True, diagnostics_dir=directory)
            detailed = IntrabarBacktester(detailed_config, self.sizer).run(candles, OneSignalEnsemble(signal))
            for filename in ("backtest_summary.json", "rejections.csv", "signals.csv", "orders.csv", "trades.csv", "events.jsonl"):
                self.assertTrue((__import__("pathlib").Path(directory) / filename).exists())
            exported_summary = __import__("json").loads((__import__("pathlib").Path(directory) / "backtest_summary.json").read_text())
            self.assertEqual(exported_summary["timestamp_convention"]["input_timestamp"], "candle_open_timestamp")
            signal_event = __import__("json").loads((__import__("pathlib").Path(directory) / "events.jsonl").read_text().splitlines()[0])
            self.assertEqual(signal_event["candle_open_timestamp"], candles[0].timestamp.isoformat())
            self.assertEqual(signal_event["decision_at_close_timestamp"], (candles[0].timestamp + timedelta(minutes=15)).isoformat())
        self.assertEqual(off.final_equity, detailed.final_equity)
        self.assertEqual(off.trades, detailed.trades)

    def test_progress_callback_reports_dataset_start_and_completion(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        candles = [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)]
        updates: list[tuple[int, int]] = []

        result = IntrabarBacktester(self.config, self.sizer).run(
            candles, OneSignalEnsemble(signal), progress_callback=lambda processed, total: updates.append((processed, total))
        )

        self.assertEqual(updates[0], (0, 2))
        self.assertEqual(updates[-1], (2, 2))
        self.assertEqual(len(updates), 3)
        self.assertEqual(len(result.trades), 1)

    def test_performance_timings_cover_the_candle_pipeline(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        timings = PerformanceTimings(True)
        result = IntrabarBacktester(self.config, self.sizer).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)],
            OneSignalEnsemble(signal),
            performance_timings=timings,
        )

        reported = result.diagnostics["performance_timings"]
        for stage in (
            "market_data_update",
            "accounting",
            "15m_feature_update",
            "4h_regime_calculation",
            "1h_structure_calculation",
            "15m_signal_generation",
            "position_sizing_risk",
            "execution_fills",
        ):
            self.assertIn(stage, reported)
            self.assertGreater(reported[stage]["calls"], 0)

    def test_incremental_market_data_matches_legacy_snapshot_results(self) -> None:
        """The cached default path must be indistinguishable from full rescans."""
        candles = []
        for index in range(1_000):
            price = 100 + ((index % 40) - 20) * 0.05 + index * 0.001
            candles.append(candle(index * 15, price, price + 0.1, price - 0.1, price))

        optimized_config = replace(self.config, legacy_snapshot_mode=False)
        legacy_config = replace(self.config, legacy_snapshot_mode=True)
        self.assertEqual(
            replace(optimized_config, legacy_snapshot_mode=False),
            replace(legacy_config, legacy_snapshot_mode=False),
        )
        # Both runs use this exact candle sequence, sizer object, and all other
        # BacktestConfig values. The mode bit selects only the snapshot path.
        legacy_ensemble = DecisionTraceEnsemble()
        optimized_ensemble = DecisionTraceEnsemble()
        legacy = IntrabarBacktester(legacy_config, self.sizer).run(candles, legacy_ensemble)
        optimized = IntrabarBacktester(optimized_config, self.sizer).run(candles, optimized_ensemble)

        self.assertGreater(len(legacy.trades), 0)
        self.assertEqual(optimized_ensemble.decisions, legacy_ensemble.decisions)
        # Trade dataclass equality covers IDs, timestamps, prices, size, fees,
        # funding, gross/net P&L, strategy and exit reason.
        self.assertEqual(optimized.trades, legacy.trades)
        self.assertEqual(optimized.initial_equity, legacy.initial_equity)
        self.assertEqual(optimized.final_equity, legacy.final_equity)
        self.assertEqual(optimized.net_pnl, legacy.net_pnl)
        self.assertEqual(optimized.return_pct, legacy.return_pct)
        self.assertEqual(optimized.equity_curve, legacy.equity_curve)
        self.assertEqual(optimized.max_drawdown_pct, legacy.max_drawdown_pct)
        self.assertEqual(optimized.kill_events, legacy.kill_events)
        self.assertEqual(optimized.rejected_orders, legacy.rejected_orders)
        self.assertEqual(optimized.partial_fills, legacy.partial_fills)

    def test_full_data_trade_window_matches_legacy_at_baseline_configuration(self) -> None:
        """Covers the real baseline trade window and its historical leverage."""
        dataset = Path(__file__).parent / "tests" / "combined.csv"
        if not dataset.exists():
            self.skipTest("combined historical fixture is not present")
        all_candles = load_ohlcv_csv(dataset)
        self.assertEqual(len(all_candles), 232_608)
        candles = all_candles[:3_000]
        common_config = BacktestConfig(
            initial_equity=10_000.0,
            fee_rate=0.0005,
            slippage_bps=5.0,
            funding_rate_per_8h=0.0001,
            candle_minutes=15,
            max_participation_rate=0.05,
            force_close_at_end=True,
        )
        legacy_config = replace(common_config, legacy_snapshot_mode=True)
        optimized_config = replace(common_config, legacy_snapshot_mode=False)
        self.assertEqual(
            replace(legacy_config, legacy_snapshot_mode=False),
            optimized_config,
        )
        baseline_sizer = PositionSizer(RiskConfig(
            risk_per_trade_pct=0.005,
            leverage=2.0,
            estimated_round_trip_cost_pct=0.002,
        ))
        legacy_trace = DecisionTraceEnsemble()
        optimized_trace = DecisionTraceEnsemble()

        legacy = IntrabarBacktester(legacy_config, baseline_sizer).run(candles, legacy_trace)
        optimized = IntrabarBacktester(optimized_config, baseline_sizer).run(candles, optimized_trace)

        self.assertEqual(legacy_trace.decisions, optimized_trace.decisions)
        self.assertEqual(legacy.trades, optimized.trades)
        self.assertEqual(legacy.initial_equity, optimized.initial_equity)
        self.assertEqual(legacy.final_equity, optimized.final_equity)
        self.assertEqual(legacy.net_pnl, optimized.net_pnl)
        self.assertEqual(legacy.return_pct, optimized.return_pct)
        self.assertEqual(legacy.equity_curve, optimized.equity_curve)
        self.assertEqual(legacy.max_drawdown_pct, optimized.max_drawdown_pct)
        self.assertEqual(legacy.kill_events, optimized.kill_events)
        self.assertEqual(legacy.rejected_orders, optimized.rejected_orders)
        self.assertEqual(legacy.partial_fills, optimized.partial_fills)
        self.assertEqual(len(legacy.trades), 92)

    def test_monte_carlo_runs_have_independent_diagnostics(self) -> None:
        signal = Signal(Side.LONG, 95, 105, 1, "test", "test entry")
        result = IntrabarBacktester(self.config, self.sizer).run(
            [candle(0, 100, 101, 99, 100), candle(15, 100, 106, 99, 105)], OneSignalEnsemble(signal)
        )
        simulation = monte_carlo_trade_order(result.trades, initial_equity=10_000, iterations=3, seed=7, diagnostic_mode=True)
        self.assertEqual(len(simulation.run_diagnostics), 3)
        self.assertEqual(simulation.run_diagnostics[0].trade_ids, (1,))
