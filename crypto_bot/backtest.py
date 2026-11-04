from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence

from .controls import KillSwitch, KillSwitchConfig, KillSwitchMode
from .diagnostics import BacktestDiagnostics, LogLevel, PerformanceTimings
from .indicators import atr, percentage_return, resample, sma, standard_deviation
from .models import BacktestResult, Candle, EntryRejectionReason, EquityPoint, Position, RejectedOrder, Side, Signal, Trade
from .regimes import RegimeClassifier
from .risk import PositionSizer, RiskConfig
from .strategies import EnsembleDecision, MarketFeatures, MarketSnapshot, StrategyEnsemble


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: float = 10_000.0
    fee_rate: float = 0.0005
    slippage_bps: float = 5.0
    funding_rate_per_8h: float = 0.0001
    candle_minutes: int = 15
    max_participation_rate: float = 0.05
    force_close_at_end: bool = True
    logging_level: LogLevel = LogLevel.INFO
    diagnostic_mode: bool = False
    diagnostics_dir: str | Path | None = None
    rejection_example_limit: int = 5
    profile_performance: bool = False
    legacy_snapshot_mode: bool = False
    kill_switch_mode: KillSwitchMode = KillSwitchMode.SAFETY

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if min(self.fee_rate, self.slippage_bps, self.max_participation_rate) < 0:
            raise ValueError("execution values cannot be negative")
        if not 0 < self.max_participation_rate <= 1:
            raise ValueError("max_participation_rate must be in (0, 1]")
        if self.candle_minutes <= 0:
            raise ValueError("candle_minutes must be positive")
        if self.rejection_example_limit <= 0:
            raise ValueError("rejection_example_limit must be positive")


@dataclass(slots=True)
class _PendingEntry:
    signal: Signal
    signal_id: int
    source_timestamp: datetime
    order_id: int
    trade_id: int
    requested_quantity: float | None = None
    remaining_quantity: float | None = None
    accepted_logged: bool = False
    request_logged: bool = False


class IntrabarBacktester:
    """Event-driven backtester with next-open execution and conservative bars.

    Signals exist at a candle close and fill at a later candle open. Stop/target
    hits are simulated from OHLC ranges; when both are reachable inside the same
    candle, the adverse stop is chosen. This deliberately avoids candle-close
    optimism. It is still not a replacement for tick/order-book validation.
    """

    def __init__(
        self,
        config: BacktestConfig | None = None,
        sizer: PositionSizer | None = None,
        regime_classifier: RegimeClassifier | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self.sizer = sizer or PositionSizer(RiskConfig())
        self.regime_classifier = regime_classifier or RegimeClassifier()
        self.kill_switch = kill_switch or KillSwitch(KillSwitchConfig())

    def run(
        self,
        candles: Sequence[Candle],
        ensemble: StrategyEnsemble | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        performance_timings: PerformanceTimings | None = None,
        warmup_candles: Sequence[Candle] = (),
    ) -> BacktestResult:
        if len(candles) < 2:
            raise ValueError("at least two candles are required")
        if any(a.timestamp >= b.timestamp for a, b in zip(candles, candles[1:])):
            raise ValueError("candles must be strictly chronological")
        if any(a.timestamp >= b.timestamp for a, b in zip(warmup_candles, warmup_candles[1:])):
            raise ValueError("warm-up candles must be strictly chronological")
        if warmup_candles and warmup_candles[-1].timestamp >= candles[0].timestamp:
            raise ValueError("warm-up candles must precede the scored window")
        ensemble = ensemble or StrategyEnsemble()
        result = BacktestResult(self.config.initial_equity, self.config.initial_equity)
        diagnostics = BacktestDiagnostics(
            self.config.logging_level,
            example_limit=self.config.rejection_example_limit,
            capture_all=self.config.diagnostic_mode,
        )
        timings = performance_timings or PerformanceTimings(self.config.profile_performance)
        closed_equity = self.config.initial_equity
        position: Position | None = None
        pending: _PendingEntry | None = None
        peak_equity = closed_equity
        next_signal_id = next_order_id = next_trade_id = 1
        known_kill_events = 0
        market_data = _IncrementalMarketData()
        # Match indicator warm-up policy for ATR safety checks too. Historical
        # candles seed the calculation but are never observed by the switch.
        atr_history = list(warmup_candles)

        # Historical context seeds indicators and higher-timeframe bars only.
        # It is deliberately excluded from account state, diagnostics, signal
        # generation, equity points, and kill-switch observation. A caller
        # should create a fresh backtester for each scored window.
        if not self.config.legacy_snapshot_mode:
            for warmup_candle in warmup_candles:
                market_data.update(warmup_candle)
                market_data.refresh_1h_features()
                market_data.refresh_4h_features(self.regime_classifier)
                market_data.refresh_15m_features()

        total_candles = len(candles)
        if progress_callback is not None:
            progress_callback(0, total_candles)

        for index, candle in enumerate(candles):
            diagnostics.counts["candles_processed"] += 1
            atr_history.append(candle)
            with timings.measure("market_data_update"):
                market_data.update(candle)
            if not self.config.legacy_snapshot_mode:
                with timings.measure("1h_structure_calculation"):
                    market_data.refresh_1h_features()
                with timings.measure("4h_regime_calculation"):
                    market_data.refresh_4h_features(self.regime_classifier)
                with timings.measure("15m_feature_update"):
                    market_data.refresh_15m_features()
            if pending is not None:
                previous_volume = candles[index - 1].volume if index > 0 else None
                position, keep_pending = self._enter(
                    pending, candle, previous_volume, closed_equity, result, diagnostics, index, position, timings,
                )
                if not keep_pending:
                    pending = None

            if position is not None:
                with timings.measure("accounting"):
                    funding_charge = self._apply_funding(position, candle)
                    if diagnostics.capture_all or diagnostics.level in (LogLevel.DEBUG, LogLevel.TRACE):
                        diagnostics.record("FUNDING_CHARGED", timestamp=candle.timestamp,
                                           candle_open_timestamp=candle.timestamp,
                                           decision_at_close_timestamp=candle.timestamp + timedelta(minutes=self.config.candle_minutes),
                                           timestamp_semantics="candle_open_timestamp",
                                           trade_id=position.trade_id, signal_id=position.signal_id,
                                           order_id=position.order_id, side=position.side,
                                           quantity=position.quantity, mark_price=candle.close,
                                           funding_rate_per_8h=self.config.funding_rate_per_8h,
                                           prorated_periods=self.config.candle_minutes / (8 * 60),
                                           funding_charge=funding_charge)
                with timings.measure("execution_fills"):
                    exit_request = self._intrabar_exit(position, candle)
                if exit_request is not None:
                    raw_price, reason = exit_request
                    with timings.measure("execution_fills"):
                        trade = self._close(position, candle.timestamp, raw_price, reason)
                    with timings.measure("accounting"):
                        result.trades.append(trade)
                        diagnostics.order("EXIT_EXECUTED", timestamp=candle.timestamp,
                                          exit_candle_open_timestamp=candle.timestamp,
                                          timestamp_semantics="exit_candle_open_timestamp",
                                          trade_id=trade.trade_id, signal_id=trade.signal_id,
                                          order_id=trade.order_id, price=trade.exit_price, reference_price=raw_price,
                                          fee=trade.exit_fee, slippage=abs(trade.exit_price - raw_price) * trade.quantity,
                                          reason=reason)
                        diagnostics.trade("POSITION_CLOSED", timestamp=candle.timestamp,
                                          exit_candle_open_timestamp=candle.timestamp,
                                          timestamp_semantics="exit_candle_open_timestamp",
                                          trade_id=trade.trade_id, signal_id=trade.signal_id,
                                          order_id=trade.order_id, net_pnl=trade.net_pnl, reason=reason)
                        closed_equity += trade.net_pnl
                    position = None
                    if pending is not None:
                        self._cancel_pending_order(pending, candle, diagnostics, "position closed before remaining quantity filled")
                        pending = None

            with timings.measure("accounting"):
                mark_equity = closed_equity
                if position is not None:
                    mark_equity += position.unrealized_gross_pnl(candle.close) - position.entry_fee - position.funding_paid
                recent_atr_candles = atr_history[-15:]
                current_atr = atr(recent_atr_candles, 14)
                atr_pct = current_atr / candle.close if current_atr else None
                self.kill_switch.observe(candle.timestamp, mark_equity, atr_pct)
                for stamp, kill_reason, message in self.kill_switch.events[known_kill_events:]:
                    event_type = "KILL_SWITCH_WOULD_TRIGGER" if self.config.kill_switch_mode is KillSwitchMode.OBSERVATIONAL else "KILL_SWITCH_ACTIVATED"
                    count_key = "kill_switch_would_trigger" if self.config.kill_switch_mode is KillSwitchMode.OBSERVATIONAL else "kill_switch_activations"
                    diagnostics.counts[count_key] += 1
                    diagnostics.record(event_type, timestamp=stamp, reason=kill_reason, detail=message,
                                       candle_open_timestamp=candle.timestamp,
                                       decision_at_close_timestamp=candle.timestamp + timedelta(minutes=self.config.candle_minutes),
                                       timestamp_semantics="candle_open_timestamp",
                                       equity=mark_equity, peak_equity=self.kill_switch.peak_equity,
                                       drawdown=1 - mark_equity / self.kill_switch.peak_equity if self.kill_switch.peak_equity else 0,
                                       drawdown_limit=self.kill_switch.config.max_drawdown_pct, current_position=position is not None,
                                       unrealized_pnl=position.unrealized_gross_pnl(candle.close) if position else 0.0,
                                       realized_pnl=closed_equity - self.config.initial_equity, open_orders=1 if pending else 0)
                known_kill_events = len(self.kill_switch.events)
                peak_equity = max(peak_equity, mark_equity)
                result.equity_curve.append(EquityPoint(candle.timestamp, mark_equity, 1 - mark_equity / peak_equity))
                if diagnostics.capture_all or diagnostics.level in (LogLevel.DEBUG, LogLevel.TRACE):
                    diagnostics.record("EQUITY_MARKED", timestamp=candle.timestamp,
                                       candle_open_timestamp=candle.timestamp,
                                       decision_at_close_timestamp=candle.timestamp + timedelta(minutes=self.config.candle_minutes),
                                       timestamp_semantics="candle_open_timestamp", equity=mark_equity,
                                       realized_equity=closed_equity,
                                       unrealized_pnl=(position.unrealized_gross_pnl(candle.close) - position.entry_fee - position.funding_paid) if position else 0.0,
                                       drawdown_pct=1 - mark_equity / peak_equity,
                                       trade_id=position.trade_id if position else None)

            # A signal observed now may only execute after this close, never on it.
            if position is None and pending is None and index < len(candles) - 1:
                # Signal generation remains observable even while the risk gate is closed.
                if self.config.legacy_snapshot_mode:
                    # Retained only as a regression oracle for the optimized path.
                    with timings.measure("15m_history_materialization"):
                        history = [*warmup_candles, *candles[: index + 1]]
                    snapshot = self._snapshot(history)
                else:
                    snapshot = self._snapshot_from_market_data(market_data)
                with timings.measure("15m_signal_generation"):
                    decision = ensemble.decide(snapshot)
                if decision.signal is not None:
                    signal_id = next_signal_id
                    next_signal_id += 1
                    diagnostics.signal(timestamp=candle.timestamp, candle_open_timestamp=candle.timestamp,
                                       decision_at_close_timestamp=candle.timestamp + timedelta(minutes=self.config.candle_minutes),
                                       timestamp_semantics="candle_open_timestamp", candle_index=index, signal_id=signal_id,
                                       direction=decision.signal.side, confidence=decision.signal.confidence, price=candle.close,
                                       strategy=decision.signal.strategy, rationale=decision.signal.rationale,
                                       stop_price=decision.signal.stop_price, take_profit=decision.signal.take_profit,
                                       strategy_state={"considered": list(decision.considered), "rejected": list(decision.rejected),
                                                       "regime": asdict(snapshot.regime)},
                                       indicator_values=asdict(snapshot.features) if snapshot.features is not None else {},
                                       market_context={"latest_1h": asdict(snapshot.candles_1h[-1]) if snapshot.candles_1h else None,
                                                       "latest_4h": asdict(snapshot.candles_4h[-1]) if snapshot.candles_4h else None})
                    allowed, reason = self._allow_new_trade()
                    if allowed:
                        pending = _PendingEntry(
                            decision.signal, signal_id, candle.timestamp, next_order_id, next_trade_id,
                        )
                        next_order_id += 1
                        next_trade_id += 1
                    else:
                        self._skip_kill_switch(diagnostics, candle, index, decision.signal, signal_id, None, None, closed_equity, reason)

            if progress_callback is not None:
                progress_callback(index + 1, total_candles)

        if position is not None and self.config.force_close_at_end:
            final_candle = candles[-1]
            with timings.measure("execution_fills"):
                trade = self._close(position, final_candle.timestamp, final_candle.close, "end_of_test")
            result.trades.append(trade)
            diagnostics.order("EXIT_EXECUTED", timestamp=final_candle.timestamp,
                              exit_candle_open_timestamp=final_candle.timestamp,
                              timestamp_semantics="exit_candle_open_timestamp",
                              trade_id=trade.trade_id, signal_id=trade.signal_id,
                              order_id=trade.order_id, price=trade.exit_price, reason="end_of_test")
            diagnostics.trade("POSITION_CLOSED", timestamp=final_candle.timestamp,
                              exit_candle_open_timestamp=final_candle.timestamp,
                              timestamp_semantics="exit_candle_open_timestamp",
                              trade_id=trade.trade_id, signal_id=trade.signal_id,
                              order_id=trade.order_id, net_pnl=trade.net_pnl, reason="end_of_test")
            closed_equity += trade.net_pnl
            result.equity_curve[-1] = EquityPoint(final_candle.timestamp, closed_equity, 1 - closed_equity / peak_equity)
        if pending is not None:
            self._cancel_pending_order(pending, candles[-1], diagnostics, "backtest ended before remaining quantity filled")
        result.final_equity = closed_equity
        result.kill_events = [(stamp, reason.value, message) for stamp, reason, message in self.kill_switch.events]
        result.diagnostics = diagnostics.summary(
            trades=result.trades,
            initial_equity=result.initial_equity,
            final_equity=closed_equity,
            net_pnl=result.net_pnl,
            return_pct=result.return_pct,
            max_drawdown_pct=result.max_drawdown_pct,
            performance_timings=timings.summary(),
        )
        result.diagnostics["timestamp_convention"] = {
            "input_timestamp": "candle_open_timestamp",
            "decision_at_close": "candle_open_timestamp + candle_minutes",
            "execution_at_next_open": "next candle's candle_open_timestamp",
            "intrabar_exit": "timestamp identifies the exit candle; exact intrabar time is unknown in OHLC data",
        }
        if self.config.diagnostics_dir is not None or self.config.diagnostic_mode:
            diagnostics.export(self.config.diagnostics_dir or "backtest_diagnostics", result.diagnostics)
        return result

    def _snapshot(self, candles: Sequence[Candle]) -> MarketSnapshot:
        candles_1h = resample(candles, 60)
        candles_4h = resample(candles, 240)
        return MarketSnapshot(
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_15m=candles,
            regime=self.regime_classifier.assess(candles_4h),
        )

    def _snapshot_from_market_data(
        self,
        market_data: "_IncrementalMarketData",
    ) -> MarketSnapshot:
        return MarketSnapshot(
            candles_4h=market_data.candles_4h,
            candles_1h=market_data.candles_1h,
            candles_15m=market_data.candles_15m,
            regime=market_data.regime,
            features=market_data.features,
        )

    def _enter(
        self,
        pending: _PendingEntry,
        candle: Candle,
        previous_volume: float | None,
        equity: float,
        result: BacktestResult,
        diagnostics: BacktestDiagnostics,
        index: int,
        position: Position | None,
        timings: PerformanceTimings,
    ) -> tuple[Position | None, bool]:
        signal, signal_id = pending.signal, pending.signal_id
        order_id, trade_id = pending.order_id, pending.trade_id
        time_fields = {
            "candle_open_timestamp": candle.timestamp,
            "timestamp_semantics": "candle_open_timestamp",
            "signal_decision_at_close_timestamp": pending.source_timestamp + timedelta(minutes=self.config.candle_minutes),
        }
        if not pending.request_logged:
            diagnostics.order("ENTRY_REQUEST", timestamp=candle.timestamp, **time_fields,
                              candle_index=index, signal_id=signal_id, order_id=order_id,
                              trade_id=trade_id, side=signal.side, price=candle.open)
            pending.request_logged = True
        allowed, reason = self._allow_new_trade()
        if not allowed:
            self._skip_kill_switch(diagnostics, candle, index, signal, signal_id, order_id, trade_id, equity, reason)
            self._cancel_pending_order(pending, candle, diagnostics, reason)
            return position, False
        with timings.measure("execution_fills"):
            entry_price = self._apply_slippage(candle.open, signal.side)
        if pending.requested_quantity is None:
            if (signal.side is Side.LONG and signal.stop_price >= entry_price) or (
                signal.side is Side.SHORT and signal.stop_price <= entry_price
            ):
                self._reject(result, diagnostics, EntryRejectionReason.INVALID_SIGNAL, candle, index, signal.strategy, signal_id, order_id, trade_id, equity, "gap made signal stop invalid", signal, entry_price)
                return None, False
            try:
                with timings.measure("position_sizing_risk"):
                    sizing = self.sizer.size(equity=equity, entry_price=entry_price, stop_price=signal.stop_price, side=signal.side)
            except ValueError as error:
                self._reject(result, diagnostics, EntryRejectionReason.RISK_LIMIT_EXCEEDED, candle, index, signal.strategy, signal_id, order_id, trade_id, equity, str(error), signal, entry_price)
                return None, False
            pending.requested_quantity = pending.remaining_quantity = sizing.quantity
        elif ((signal.side is Side.LONG and signal.stop_price >= entry_price) or (
            signal.side is Side.SHORT and signal.stop_price <= entry_price
        )):
            self._cancel_pending_order(pending, candle, diagnostics, "gap made remaining order invalid relative to stop")
            return None, False

        # At this open, only the preceding completed candle's volume is known.
        available_quantity = (previous_volume or 0.0) * self.config.max_participation_rate
        quantity = min(pending.remaining_quantity or 0.0, available_quantity)
        if quantity <= 0:
            diagnostics.order("ORDER_WAITING_FOR_LIQUIDITY", timestamp=candle.timestamp, **time_fields,
                              signal_id=signal_id, order_id=order_id, trade_id=trade_id,
                              remaining_quantity=pending.remaining_quantity, prior_candle_volume=previous_volume)
            return position, True

        if not pending.accepted_logged:
            diagnostics.entry_accepted(timestamp=candle.timestamp, **time_fields, candle_index=index,
                                       signal_id=signal_id, order_id=order_id, trade_id=trade_id,
                                       strategy=signal.strategy, price=entry_price, quantity=quantity,
                                       requested_quantity=pending.requested_quantity,
                                       order_value=quantity * entry_price)
            pending.accepted_logged = True

        is_partial = quantity < (pending.remaining_quantity or 0.0)
        if is_partial:
            result.partial_fills += 1
            diagnostics.order("PARTIAL_FILL", timestamp=candle.timestamp, **time_fields,
                              signal_id=signal_id, order_id=order_id, trade_id=trade_id,
                              requested_size=pending.requested_quantity, filled_size=quantity,
                              price=entry_price, reference_price=candle.open,
                              fee=quantity * entry_price * self.config.fee_rate,
                              slippage=abs(entry_price - candle.open) * quantity,
                              remaining_quantity=(pending.remaining_quantity or 0.0) - quantity,
                              prior_candle_volume=previous_volume)
        else:
            diagnostics.order("ORDER_FILLED", timestamp=candle.timestamp, **time_fields,
                              signal_id=signal_id, order_id=order_id, trade_id=trade_id,
                              price=entry_price, reference_price=candle.open,
                              fee=quantity * entry_price * self.config.fee_rate,
                              slippage=abs(entry_price - candle.open) * quantity,
                              fill_quantity=quantity,
                              requested_size=pending.requested_quantity)

        fee = quantity * entry_price * self.config.fee_rate
        if position is None:
            position = Position(
                side=signal.side, quantity=quantity, entry_price=entry_price,
                stop_price=signal.stop_price, take_profit=signal.take_profit,
                opened_at=candle.timestamp, strategy=signal.strategy, entry_fee=fee,
                trade_id=trade_id, signal_id=signal_id, order_id=order_id,
            )
            diagnostics.trade("POSITION_OPENED", timestamp=candle.timestamp, **time_fields,
                              trade_id=trade_id, signal_id=signal_id, order_id=order_id,
                              price=entry_price, quantity=quantity)
        else:
            old_quantity = position.quantity
            position.entry_price = (position.entry_price * old_quantity + entry_price * quantity) / (old_quantity + quantity)
            position.quantity += quantity
            position.entry_fee += fee
            diagnostics.trade("POSITION_INCREASED", timestamp=candle.timestamp, **time_fields,
                              trade_id=trade_id, signal_id=signal_id, order_id=order_id,
                              price=entry_price, quantity=quantity, total_quantity=position.quantity,
                              average_entry_price=position.entry_price)
        pending.remaining_quantity = max(0.0, (pending.remaining_quantity or 0.0) - quantity)
        return position, pending.remaining_quantity > 0

    @staticmethod
    def _cancel_pending_order(pending: _PendingEntry, candle: Candle, diagnostics: BacktestDiagnostics, reason: str) -> None:
        if pending.remaining_quantity:
            diagnostics.order("ORDER_CANCELLED", timestamp=candle.timestamp,
                              candle_open_timestamp=candle.timestamp,
                              timestamp_semantics="candle_open_timestamp",
                              signal_id=pending.signal_id, order_id=pending.order_id,
                              trade_id=pending.trade_id, remaining_quantity=pending.remaining_quantity,
                              reason=reason)

    def _allow_new_trade(self) -> tuple[bool, str]:
        if self.config.kill_switch_mode is KillSwitchMode.OBSERVATIONAL:
            allowed, reason = self.kill_switch.allow_new_trade()
            return True, "research mode: " + reason if not allowed else "research mode: kill switch clear"
        return self.kill_switch.allow_new_trade()

    def _skip_kill_switch(
        self,
        diagnostics: BacktestDiagnostics,
        candle: Candle,
        index: int,
        signal: Signal,
        signal_id: int | None,
        order_id: int | None,
        trade_id: int | None,
        equity: float,
        detail: str,
    ) -> None:
        diagnostics.entry_skipped_kill_switch(
            timestamp=candle.timestamp,
            candle_open_timestamp=candle.timestamp,
            decision_at_close_timestamp=candle.timestamp + timedelta(minutes=self.config.candle_minutes),
            timestamp_semantics="candle_open_timestamp",
            candle_index=index,
            strategy=signal.strategy,
            signal_id=signal_id,
            order_id=order_id,
            trade_id=trade_id,
            price=candle.close,
            signal_direction=signal.side,
            equity=equity,
            detail=detail,
            kill_switch_active=True,
        )

    def _apply_funding(self, position: Position, candle: Candle) -> float:
        periods = self.config.candle_minutes / (8 * 60)
        # Positive funding is a cost for longs and a credit for shorts.
        charge = position.side.sign * position.quantity * candle.close * self.config.funding_rate_per_8h * periods
        position.funding_paid += charge
        return charge

    def _intrabar_exit(self, position: Position, candle: Candle) -> tuple[float, str] | None:
        if position.side is Side.LONG:
            stopped = candle.low <= position.stop_price
            targeted = position.take_profit is not None and candle.high >= position.take_profit
            if stopped:  # pessimistic ordering when both stop and target occur in one bar
                return min(position.stop_price, candle.open), "stop_loss"
            if targeted:
                return position.take_profit or candle.high, "take_profit"
        else:
            stopped = candle.high >= position.stop_price
            targeted = position.take_profit is not None and candle.low <= position.take_profit
            if stopped:
                return max(position.stop_price, candle.open), "stop_loss"
            if targeted:
                return position.take_profit or candle.low, "take_profit"
        return None

    def _close(self, position: Position, timestamp: datetime, raw_price: float, reason: str) -> Trade:
        exit_side = Side.SHORT if position.side is Side.LONG else Side.LONG
        exit_price = self._apply_slippage(raw_price, exit_side)
        exit_fee = position.quantity * exit_price * self.config.fee_rate
        gross_pnl = position.side.sign * position.quantity * (exit_price - position.entry_price)
        net_pnl = gross_pnl - position.entry_fee - exit_fee - position.funding_paid
        return Trade(
            side=position.side,
            quantity=position.quantity,
            entry_time=position.opened_at,
            exit_time=timestamp,
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_price=position.stop_price,
            take_profit=position.take_profit,
            strategy=position.strategy,
            exit_reason=reason,
            gross_pnl=gross_pnl,
            entry_fee=position.entry_fee,
            exit_fee=exit_fee,
            funding_paid=position.funding_paid,
            net_pnl=net_pnl,
            trade_id=position.trade_id,
            signal_id=position.signal_id,
            order_id=position.order_id,
        )

    def _reject(
        self,
        result: BacktestResult,
        diagnostics: BacktestDiagnostics,
        rejection_reason: EntryRejectionReason,
        candle: Candle,
        index: int,
        strategy: str,
        signal_id: int | None,
        order_id: int | None,
        trade_id: int | None,
        equity: float,
        detail: str,
        signal: Signal | None = None,
        price: float | None = None,
        sizing: object | None = None,
    ) -> None:
        """Record one, and only one, typed reason for each rejected entry."""
        result.rejected_orders.append(RejectedOrder(candle.timestamp, strategy, rejection_reason))
        risk = self.sizer.config
        requested_size = getattr(sizing, "quantity", None)
        requested_value = getattr(sizing, "notional", None)
        diagnostics.rejection(
            rejection_reason, timestamp=candle.timestamp, candle_index=index, strategy=strategy,
            signal_id=signal_id, order_id=order_id, trade_id=trade_id, price=price if price is not None else candle.close,
            signal_direction=signal.side if signal else None, current_position=False, equity=equity,
            available_cash=equity, requested_position_size=requested_size, requested_order_value=requested_value,
            detail=detail, kill_switch_active=self.kill_switch.is_halted,
            risk_values={"risk_per_trade_pct": risk.risk_per_trade_pct, "max_position_notional_pct": risk.max_position_notional_pct,
                         "max_margin_fraction": risk.max_margin_fraction, "leverage": risk.leverage},
        )

    def _apply_slippage(self, price: float, order_side: Side) -> float:
        adjustment = self.config.slippage_bps / 10_000
        return price * (1 + adjustment if order_side is Side.LONG else 1 - adjustment)


class _IncrementalMarketData:
    """Maintains resampled bars and cacheable indicator inputs in O(1) per bar.

    The current 1H and 4H buckets deliberately remain mutable until their last
    constituent 15m candle has closed. That is the pre-existing snapshot
    semantics, retained here so the optimized and legacy engines are identical.
    """

    def __init__(self) -> None:
        self.candles_15m: list[Candle] = []
        self.candles_1h: list[Candle] = []
        self.candles_4h: list[Candle] = []
        self._state_1h: tuple[object, float, float, float, float, float] | None = None
        self._state_4h: tuple[object, float, float, float, float, float] | None = None
        self._ema_1h_fast = _StreamingEMA(20)
        self._ema_1h_slow = _StreamingEMA(50)
        self._ema_4h_fast = _StreamingEMA(20)
        self._ema_4h_slow = _StreamingEMA(50)
        self._trend_fast_1h: float | None = None
        self._trend_slow_1h: float | None = None
        self._regime_fast_4h: float | None = None
        self._regime_slow_4h: float | None = None
        self.regime = self._unknown_regime()
        self.features = MarketFeatures(None, None, None, None, None, None, None, None, None)

    def update(self, candle: Candle) -> None:
        self.candles_15m.append(candle)
        self._state_1h = self._update_series(candle, 60, self.candles_1h, self._state_1h)
        self._state_4h = self._update_series(candle, 240, self.candles_4h, self._state_4h)

    def refresh_1h_features(self) -> None:
        self._trend_fast_1h = self._ema_1h_fast.update(self.candles_1h)
        self._trend_slow_1h = self._ema_1h_slow.update(self.candles_1h)

    def refresh_4h_features(self, classifier: RegimeClassifier) -> None:
        self._regime_fast_4h = self._ema_4h_fast.update(self.candles_4h)
        self._regime_slow_4h = self._ema_4h_slow.update(self.candles_4h)
        if len(self.candles_4h) < 2:
            self.regime = self._unknown_regime()
            return
        closes = self.candles_4h
        self.regime = classifier.assess_precomputed(
            candle_count=len(closes),
            latest_close=closes[-1].close,
            previous_close=closes[-2].close,
            fast_ema=self._regime_fast_4h,
            slow_ema=self._regime_slow_4h,
            average_true_range=atr(closes, classifier.config.atr_period),
            momentum=percentage_return([candle.close for candle in closes[-(classifier.config.fast_ema + 1):]], classifier.config.fast_ema),
        )

    def refresh_15m_features(self) -> None:
        candles = self.candles_15m
        execution_atr = atr(candles, 14)
        if len(candles) >= 22:
            previous = candles[-21:-1]
            previous_high = max(candle.high for candle in previous)
            previous_low = min(candle.low for candle in previous)
            baseline_volume = sma([candle.volume or 0 for candle in previous], 20)
        else:
            previous_high = previous_low = baseline_volume = None
        closes_20 = [candle.close for candle in candles[-20:]]
        self.features = MarketFeatures(
            execution_atr=execution_atr,
            trend_fast_1h=self._trend_fast_1h,
            trend_slow_1h=self._trend_slow_1h,
            previous_high_15m=previous_high,
            previous_low_15m=previous_low,
            baseline_volume_15m=baseline_volume,
            momentum_15m=percentage_return([candle.close for candle in candles[-13:]], 12),
            mean_15m=sma(closes_20, 20),
            deviation_15m=standard_deviation(closes_20, 20),
        )

    @staticmethod
    def _unknown_regime():
        # Avoid carrying a classifier instance solely for the insufficient-data case.
        from .regimes import MarketRegime, RegimeAssessment
        return RegimeAssessment(MarketRegime.UNKNOWN, 0.0, 0.0, 0.0, "insufficient higher-timeframe history")

    @staticmethod
    def _bucket_timestamp(candle: Candle, minutes: int):
        stamp = candle.timestamp.replace(second=0, microsecond=0)
        hours = minutes // 60
        return stamp.replace(hour=stamp.hour - (stamp.hour % hours), minute=0)

    def _update_series(
        self,
        candle: Candle,
        minutes: int,
        series: list[Candle],
        state: tuple[object, float, float, float, float, float] | None,
    ) -> tuple[object, float, float, float, float, float]:
        timestamp = self._bucket_timestamp(candle, minutes)
        if state is None or state[0] != timestamp:
            state = (timestamp, candle.open, candle.high, candle.low, candle.close, candle.volume or 0.0)
            series.append(Candle(timestamp, candle.open, candle.high, candle.low, candle.close, candle.volume or 0.0))
        else:
            _, opening, high, low, _, volume = state
            state = (timestamp, opening, max(high, candle.high), min(low, candle.low), candle.close, volume + (candle.volume or 0.0))
            _, opening, high, low, closing, volume = state
            series[-1] = Candle(timestamp, opening, high, low, closing, volume)
        return state


class _StreamingEMA:
    """Exact EMA recurrence for a series whose last candle is updated in place."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.multiplier = 2 / (period + 1)
        self._length = 0
        self._previous_ema: float | None = None
        self._value: float | None = None

    def update(self, candles: Sequence[Candle]) -> float | None:
        length = len(candles)
        if not length:
            self._length = 0
            self._previous_ema = self._value = None
            return None
        latest = candles[-1].close
        if length != self._length:
            if self._length == 0:
                self._value = latest
            else:
                self._previous_ema = self._value
                self._value = latest * self.multiplier + self._previous_ema * (1 - self.multiplier)
            self._length = length
        elif length == 1:
            self._value = latest
        else:
            self._value = latest * self.multiplier + self._previous_ema * (1 - self.multiplier)
        return self._value if length >= self.period else None
