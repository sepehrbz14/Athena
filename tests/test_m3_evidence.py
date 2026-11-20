from __future__ import annotations

import unittest

from crypto_bot.m3_accounting import PairedWeeklyAccountEvidence, WeeklyAccountEvidence
from crypto_bot.m3_counterfactual import CounterfactualOpportunityEvidence
from crypto_bot.m3_evidence import EvidenceValidationError, reconcile_weekly_accounting, validate_paired_opportunity_evidence
from crypto_bot.m3_policy import OpportunityDecision
from crypto_bot.m3_predictive import PredictiveOpportunityEvidence
from crypto_bot.m3_portfolio import OpportunityExecutionDisposition
from dataclasses import replace


class M3EvidenceTests(unittest.TestCase):
    def fixture(self):
        decision = OpportunityDecision("o1", "BTC", "2025-01-01T00:15:00Z", "2025-W01", "ABSTAINED", "test", None)
        prediction = PredictiveOpportunityEvidence("o1", "2025-W01", "SCORED_ZERO", "eligible abstention", None, "0")
        cf = CounterfactualOpportunityEvidence("o1", "2025-W01", "ABSTAINED", None, None, None, None,
                                                 None, None, None, None, None, None)
        exe = OpportunityExecutionDisposition("o1", "2025-W01", "ABSTAINED", "test", None, None, None)
        args = dict(opportunity_ids=("o1",), utc_week_by_id={"o1": "2025-W01"},
            candidate_decisions=(decision,), incumbent_decisions=(decision,),
            candidate_predictive=(prediction,), incumbent_predictive=(prediction,),
            candidate_counterfactual=(cf,), incumbent_counterfactual=(cf,),
            candidate_executable=(exe,), incumbent_executable=(exe,))
        return args

    def test_all_lanes_reconcile_to_one_shared_opportunity(self):
        rows = validate_paired_opportunity_evidence(**self.fixture())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].opportunity_id, "o1")

    def test_missing_opportunity_or_signal_identity_fails_closed(self):
        args = self.fixture()
        args["candidate_predictive"] = ()
        with self.assertRaisesRegex(EvidenceValidationError, "row counts"):
            validate_paired_opportunity_evidence(**args)
        args = self.fixture()
        args["candidate_executable"] = (replace(args["candidate_executable"][0], signal_id=9),)
        with self.assertRaisesRegex(EvidenceValidationError, "does not identify"):
            validate_paired_opportunity_evidence(**args)

    def test_weekly_mark_to_market_and_paired_delta_reconcile(self):
        candidate = WeeklyAccountEvidence("2025-W01", "a", "b", "1000", "1010", "0.01", True, False)
        incumbent = WeeklyAccountEvidence("2025-W01", "a", "b", "1000", "1000", "0", False, False)
        reconcile_weekly_accounting((PairedWeeklyAccountEvidence("2025-W01", candidate, incumbent, "0.01"),))
        with self.assertRaisesRegex(EvidenceValidationError, "delta"):
            reconcile_weekly_accounting((PairedWeeklyAccountEvidence("2025-W01", candidate, incumbent, "0.02"),))


if __name__ == "__main__":
    unittest.main()
