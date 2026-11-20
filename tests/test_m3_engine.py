from __future__ import annotations

import unittest

from crypto_bot.m3_admission import AdmittedAttempt
from crypto_bot.m3_chronology import FoldWindow
from crypto_bot.m3_engine import FoldEvaluationError, evaluate_fold_evidence


class M3EngineTests(unittest.TestCase):
    def test_fold_engine_fails_before_evaluation_without_signed_isolation_permit(self):
        admission = AdmittedAttempt("a", "p", "c", "c"*64, "p"*64, "r"*64, "k"*64,
            (), "previously_observed_historical_diagnostic", True, True, False,
            b"{}", b"{}")
        boundary = __import__("datetime").datetime(2025, 1, 1, tzinfo=__import__("datetime").timezone.utc)
        fold = FoldWindow("fold-1", (boundary, boundary), (boundary, boundary),
                          (boundary, boundary), (boundary, boundary), (boundary, boundary))
        with self.assertRaisesRegex(FoldEvaluationError, "permit is absent"):
            evaluate_fold_evidence(admission, b"{}", fold, instrument_id="BTCUSDT",
                dataset_manifest_sha256="d"*64, permit=None)


if __name__ == "__main__":
    unittest.main()
