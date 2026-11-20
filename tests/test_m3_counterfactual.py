from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from io import StringIO
from pathlib import Path
import unittest

from crypto_bot.m3_admission import AdmittedAttempt, PinnedInput
from crypto_bot.m3_chronology import BAR, Opportunity, OpportunityUniverse, SignalRef
from crypto_bot.m3_policy import OpportunityDecision
from crypto_bot.m3_counterfactual import (
    CounterfactualEvidenceError, pair_counterfactual_opportunities,
    score_counterfactual_decisions,
)


UTC = timezone.utc
T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _signal(i, strategy="breakout"):
    return SignalRef(i, strategy, T0, T0 + BAR, "long", "0.7", "range", "100", "95", "110", False)


def _decision(oid, signal, week="2024-W01"):
    selected = signal is not None
    return OpportunityDecision(oid, "BTCUSDT", (T0 + BAR).isoformat().replace("+00:00", "Z"), week,
                               "SELECTED" if selected else "ABSTAINED", "test", signal)


def _trade(signal_id=1, entry="2024-01-01T00:15:00Z", exit="2024-01-01T01:00:00Z"):
    return {"signal_id": signal_id, "strategy": "breakout", "direction": "long",
            "signal_timestamp": "2024-01-01T00:00:00Z", "entry_timestamp": entry,
            "exit_timestamp": exit, "execution_gross_pnl": "10", "gross_pnl": "11",
            "fees": "1", "slippage": "1", "funding_paid": "-2", "net_pnl": "11",
            "exit_reason": "take_profit"}


def _admission(rows):
    stream = StringIO(newline="")
    fields = list(_trade().keys())
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    ledger = stream.getvalue().encode()
    metadata = {"configuration": {
        "fee_bps": 5, "slippage_bps": 5, "funding_bps_8h": 1,
        "max_participation_rate": 0.05,
        "overlap_policy": "each signal is a separate independent counterfactual trade; no shared capital or aggregate liquidity; overlaps retained",
    }}
    metadata_bytes = json.dumps(metadata, sort_keys=True).encode()
    protocol = {"cost_policy": {"fee_bps_per_side": 5, "slippage_bps_per_side": 5,
                                "funding_bps_per_8h": 1, "max_participation_percent": 5}}
    protocol_bytes = json.dumps(protocol, sort_keys=True).encode()
    inputs = []
    for key, data in (("counterfactual_ledger", ledger), ("counterfactual_metadata", metadata_bytes)):
        inputs.append(PinnedInput(key, Path("unused"), hashlib.sha256(data).hexdigest(), len(data), data))
    return AdmittedAttempt("attempt", "protocol", "candidate", "a"*64, "b"*64, "c"*64,
                           "d"*64, tuple(inputs), "previously_observed_historical_diagnostic",
                           True, True, False, b"{}", protocol_bytes)


class M3CounterfactualTests(unittest.TestCase):
    def test_candidate_and_incumbent_pair_every_opportunity_and_retain_abstentions(self):
        s1, s2 = _signal(1), _signal(2)
        opportunities = OpportunityUniverse((
            Opportunity("a", "BTCUSDT", T0 + BAR, (s1,), "2024-W01"),
            Opportunity("b", "BTCUSDT", T0 + BAR, (s2,), "2024-W01"),
        ), ("2024-W01",))
        candidate = (_decision("a", s1), _decision("b", None))
        incumbent = (_decision("a", None), _decision("b", s2))
        admission = _admission([_trade(1), _trade(2)])
        interval = (T0, T0 + timedelta(days=1))
        candidate_rows = score_counterfactual_decisions(admission, candidate, opportunities,
                                                         scored_interval=interval)
        incumbent_rows = score_counterfactual_decisions(admission, incumbent, opportunities,
                                                          scored_interval=interval)
        paired = pair_counterfactual_opportunities(candidate_rows, incumbent_rows)
        self.assertEqual([row.opportunity_id for row in paired], ["a", "b"])
        self.assertEqual([row.candidate.status for row in paired], ["COMPLETED", "ABSTAINED"])
        self.assertEqual([row.incumbent.status for row in paired], ["ABSTAINED", "COMPLETED"])
        self.assertEqual(paired[0].candidate.net_pnl, "11")
        self.assertEqual(paired[0].candidate.funding_contribution, "2")

    def test_counterfactual_absent_row_and_boundary_crossing_are_explicit(self):
        s1, s2 = _signal(1), _signal(2)
        opportunities = OpportunityUniverse((
            Opportunity("a", "BTCUSDT", T0 + BAR, (s1,), "2024-W01"),
            Opportunity("b", "BTCUSDT", T0 + BAR, (s2,), "2024-W01"),
        ), ("2024-W01",))
        decisions = (_decision("a", s1), _decision("b", s2))
        admission = _admission([_trade(1, exit="2024-01-01T02:00:00Z")])
        rows = score_counterfactual_decisions(admission, decisions, opportunities,
                                              scored_interval=(T0, T0 + timedelta(hours=1)))
        self.assertEqual([row.status for row in rows], ["PURGED", "NO_COUNTERFACTUAL_TRADE"])
        self.assertEqual(len(rows), len(opportunities.opportunities))

    def test_invalid_cost_assumptions_or_ledger_accounting_fail_closed(self):
        s1 = _signal(1)
        opportunities = OpportunityUniverse((Opportunity("a", "BTCUSDT", T0 + BAR, (s1,), "2024-W01"),),
                                             ("2024-W01",))
        decision = (_decision("a", s1),)
        admission = _admission([_trade()])
        from dataclasses import replace
        altered = replace(admission, protocol_bytes=json.dumps({"cost_policy": {
            "fee_bps_per_side": 0, "slippage_bps_per_side": 5, "funding_bps_per_8h": 1,
            "max_participation_percent": 5}}).encode())
        with self.assertRaisesRegex(CounterfactualEvidenceError, "changes baseline"):
            score_counterfactual_decisions(altered, decision, opportunities,
                                           scored_interval=(T0, T0 + timedelta(days=1)))

        inconsistent = _trade()
        inconsistent["net_pnl"] = "12"
        with self.assertRaisesRegex(CounterfactualEvidenceError, "does not reconcile"):
            score_counterfactual_decisions(_admission([inconsistent]), decision, opportunities,
                                           scored_interval=(T0, T0 + timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
