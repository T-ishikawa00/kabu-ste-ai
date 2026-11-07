"""Tests for the newly created, in-memory offline pipeline only."""

from __future__ import annotations

import math
import unittest

from tools.offline_pipeline import (
    AUXILIARY_TITLE,
    Fill,
    MarketEvent,
    OfflinePipeline,
    OrderIntent,
    PolicyStub,
    PositionLedger,
    RiskGate,
)


def artificial_scenario() -> tuple[MarketEvent, ...]:
    return (
        MarketEvent(100.0, 100.0, 1000.0, 1000.0, 0.0),
        MarketEvent(102.0, 100.0, 1000.0, 1000.0, 0.0),
        MarketEvent(100.0, 100.0, 1000.0, 1000.0, 0.0),
    )


class MarketEventValidationTests(unittest.TestCase):
    def test_rejects_nonfinite_or_invalid_synthetic_inputs(self) -> None:
        with self.assertRaises(ValueError):
            MarketEvent(math.nan, 1.0, 1.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            MarketEvent(0.0, 1.0, 1.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            MarketEvent(1.0, -1.0, 1.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            MarketEvent(1.0, 1.0, 1.0, 1.0, -0.1)
        with self.assertRaises(ValueError):
            MarketEvent(True, 1.0, 1.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            MarketEvent(10**10000, 1.0, 1.0, 1.0, 1.0)


class RiskGateTests(unittest.TestCase):
    def test_rejects_exposure_at_or_beyond_the_loss_limit(self) -> None:
        gate = RiskGate(max_units=2, loss_limit=10.0)
        buy = OrderIntent("BUY", 1)

        self.assertEqual(gate.rejection_reason(buy, 0, -10.0), "LOSS_LIMIT")
        self.assertEqual(gate.rejection_reason(buy, 0, -10.01), "LOSS_LIMIT")
        self.assertIsNone(gate.rejection_reason(buy, 0, -9.99))

    def test_rejects_order_that_would_exceed_max_units(self) -> None:
        gate = RiskGate(max_units=2, loss_limit=10.0)
        too_large = OrderIntent("BUY", 3)
        at_limit = OrderIntent("BUY", 2)

        self.assertEqual(gate.rejection_reason(too_large, 0, 0.0), "MAX_UNITS")
        self.assertIsNone(gate.rejection_reason(at_limit, 0, 0.0))

    def test_loss_limit_still_allows_exposure_reduction(self) -> None:
        gate = RiskGate(max_units=2, loss_limit=10.0)
        close_long = OrderIntent("SELL", 1)

        self.assertIsNone(gate.rejection_reason(close_long, 1, -10.0))

    def test_invalid_intents_are_rejected_without_ledger_mutation(self) -> None:
        gate = RiskGate(max_units=2, loss_limit=10.0)
        ledger = PositionLedger(quantity=1, average_entry_price=100.0, realized_pnl=-3.0)
        before = (ledger.quantity, ledger.average_entry_price, ledger.realized_pnl)

        self.assertEqual(
            gate.rejection_reason(OrderIntent("HOLD", 1), ledger.quantity, ledger.realized_pnl),
            "INVALID_SIDE",
        )
        self.assertEqual(
            gate.rejection_reason(OrderIntent("BUY", 0), ledger.quantity, ledger.realized_pnl),
            "INVALID_QUANTITY",
        )
        self.assertEqual(
            (ledger.quantity, ledger.average_entry_price, ledger.realized_pnl), before
        )


class PositionLedgerTests(unittest.TestCase):
    def test_weighted_entry_long_close_and_short_reversal(self) -> None:
        ledger = PositionLedger()

        ledger.apply(Fill(1, "BUY", 2, 100.0))
        ledger.apply(Fill(2, "BUY", 1, 130.0))
        self.assertEqual(ledger.quantity, 3)
        self.assertAlmostEqual(ledger.average_entry_price, 110.0)

        ledger.apply(Fill(3, "SELL", 1, 120.0))
        self.assertEqual(ledger.quantity, 2)
        self.assertAlmostEqual(ledger.average_entry_price, 110.0)
        self.assertAlmostEqual(ledger.realized_pnl, 10.0)

        ledger.apply(Fill(4, "SELL", 3, 105.0))
        self.assertEqual(ledger.quantity, -1)
        self.assertAlmostEqual(ledger.average_entry_price, 105.0)
        self.assertAlmostEqual(ledger.realized_pnl, 0.0)

        ledger.apply(Fill(5, "BUY", 1, 95.0))
        self.assertEqual(ledger.quantity, 0)
        self.assertEqual(ledger.average_entry_price, 0.0)
        self.assertAlmostEqual(ledger.realized_pnl, 10.0)


class OfflinePipelineTests(unittest.TestCase):
    def test_empty_scenario_has_no_fills_or_position(self) -> None:
        report = OfflinePipeline().run(()).as_dict()

        self.assertEqual(report["features"], [])
        self.assertEqual(report["event_log"], [])
        self.assertEqual(report["fills"], [])
        self.assertEqual(report["position"]["quantity"], 0)
        self.assertEqual(report["position"]["average_entry_price"], 0.0)
        self.assertEqual(report["position"]["realized_pnl"], 0.0)

    def test_fixed_artificial_events_are_deterministic_and_labeled(self) -> None:
        pipeline = OfflinePipeline()
        first = pipeline.run(artificial_scenario()).as_dict()
        second = pipeline.run(artificial_scenario()).as_dict()

        self.assertEqual(first, second)
        self.assertEqual(first["title"], AUXILIARY_TITLE)
        self.assertIn("PPO推論", AUXILIARY_TITLE)
        self.assertIn("実売買", AUXILIARY_TITLE)
        self.assertEqual([entry["status"] for entry in first["event_log"]], ["HOLD", "FILLED", "FILLED"])
        self.assertEqual(len(first["fills"]), 2)
        self.assertEqual(first["position"]["quantity"], 0)
        self.assertAlmostEqual(first["position"]["realized_pnl"], -2.0)

    def test_loss_rejected_order_does_not_mutate_ledger(self) -> None:
        gate = RiskGate(max_units=2, loss_limit=2.0)
        report = OfflinePipeline(risk_gate=gate).run(
            artificial_scenario()
            + (MarketEvent(101.0, 100.0, 1000.0, 1000.0, 0.0),)
        ).as_dict()

        rejected = report["event_log"][-1]
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(rejected["reason"], "LOSS_LIMIT")
        self.assertEqual(rejected["position_before"], rejected["position_after"])
        self.assertEqual(rejected["realized_pnl_before"], rejected["realized_pnl_after"])
        self.assertEqual(len(report["fills"]), 2)
        self.assertEqual(report["position"]["quantity"], 0)
        self.assertAlmostEqual(report["position"]["realized_pnl"], -2.0)

    def test_max_units_rejection_does_not_mutate_position(self) -> None:
        oversized_policy = PolicyStub(buy_units=3)
        gate = RiskGate(max_units=2, loss_limit=100.0)
        report = OfflinePipeline(risk_gate=gate, policy=oversized_policy).run(
            artificial_scenario()[:2]
        ).as_dict()

        rejected = report["event_log"][-1]
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(rejected["reason"], "MAX_UNITS")
        self.assertEqual(rejected["position_before"], 0)
        self.assertEqual(rejected["position_after"], 0)
        self.assertEqual(report["fills"], [])
        self.assertEqual(report["position"]["quantity"], 0)

    def test_pipeline_rejects_non_event_objects(self) -> None:
        with self.assertRaises(ValueError):
            OfflinePipeline().run((object(),))


if __name__ == "__main__":
    unittest.main()
