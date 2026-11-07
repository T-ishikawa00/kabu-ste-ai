"""Synthetic-input checks for AST-extracted scalar source functions only.

No historical module is imported. ``load_function`` returns each approved
function from its source AST under the repository's static allowlist.
"""

from __future__ import annotations

import unittest
from types import MethodType, SimpleNamespace

from tools.safe_ast import load_function


class ExtractedLegacyFunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.norm = staticmethod(load_function("features.py", "_norm"))
        cls.temperature_scale = staticmethod(load_function(
            "market_temperature.py", "MarketTemperature._scale01"
        ))
        cls.temperature_compute = staticmethod(load_function(
            "market_temperature.py", "MarketTemperature.compute"
        ))
        cls.risk_features = staticmethod(load_function("risk_sizer.py", "RiskSizer._features"))
        cls.strategy_size = staticmethod(load_function(
            "risk_sizer.py", "RiskSizer.size_for_strategy"
        ))
        cls.realized_pnl = staticmethod(load_function(
            "step2_env.py", "TradingEnv._calc_realized_pnl"
        ))
        cls.router_decide = staticmethod(load_function(
            "strategy_router.py", "StrategyRouter.decide"
        ))

    def test_norm_clamps_endpoints_equal_bounds_and_reversed_interval(self) -> None:
        self.assertEqual(self.norm(-1.0, 0.0, 10.0), 0.0)
        self.assertEqual(self.norm(0.0, 0.0, 10.0), 0.0)
        self.assertEqual(self.norm(5.0, 0.0, 10.0), 0.5)
        self.assertEqual(self.norm(10.0, 0.0, 10.0), 1.0)
        self.assertEqual(self.norm(11.0, 0.0, 10.0), 1.0)
        self.assertEqual(self.norm(4.0, 4.0, 4.0), 0.0)
        self.assertEqual(self.norm(10.0, 10.0, 0.0), 0.0)
        self.assertEqual(self.norm(5.0, 10.0, 0.0), 0.5)
        self.assertEqual(self.norm(0.0, 10.0, 0.0), 1.0)

    def test_market_temperature_scale_clamps_inverts_and_handles_equal_bounds(self) -> None:
        state = SimpleNamespace()
        scale = self.temperature_scale

        self.assertEqual(scale(state, -1.0, 0.0, 10.0), 0.0)
        self.assertEqual(scale(state, 0.0, 0.0, 10.0), 0.0)
        self.assertEqual(scale(state, 5.0, 0.0, 10.0), 0.5)
        self.assertEqual(scale(state, 10.0, 0.0, 10.0), 1.0)
        self.assertEqual(scale(state, 11.0, 0.0, 10.0), 1.0)
        self.assertEqual(scale(state, 10.0, 10.0, 10.0), 0.0)
        self.assertEqual(scale(state, 0.0, 0.0, 10.0, invert=True), 1.0)
        self.assertEqual(scale(state, 10.0, 0.0, 10.0, invert=True), 0.0)
        self.assertEqual(scale(state, 5.0, 10.0, 0.0), 0.5)

    def _temperature_self(self) -> SimpleNamespace:
        state = SimpleNamespace(
            vol_cold=50.0,
            vol_hot=500.0,
            depth_cold=50.0,
            depth_hot=500.0,
            spread_hot=1.0,
            spread_cold=5.0,
        )
        state._scale01 = MethodType(self.temperature_scale, state)
        return state

    def test_market_temperature_compute_at_cold_and_hot_endpoints(self) -> None:
        state = self._temperature_self()
        cold = {
            "vol_per_sec": 50.0,
            "bid_avg": 50.0,
            "ask_avg": 50.0,
            "spread_avg": 5.0,
        }
        hot = {
            "vol_per_sec": 500.0,
            "bid_avg": 500.0,
            "ask_avg": 500.0,
            "spread_avg": 1.0,
        }

        cold_result = self.temperature_compute(state, "synthetic", {}, cold)
        hot_result = self.temperature_compute(state, "synthetic", {}, hot)

        self.assertEqual(cold_result, {
            "market_heat": 0.0,
            "vol_score": 0.0,
            "depth_score": 0.0,
            "spread_score": 0.0,
        })
        self.assertEqual(hot_result, {
            "market_heat": 1.0,
            "vol_score": 1.0,
            "depth_score": 1.0,
            "spread_score": 1.0,
        })

    def test_market_temperature_missing_input_key_fails_explicitly(self) -> None:
        state = self._temperature_self()
        with self.assertRaises(KeyError):
            self.temperature_compute(state, "synthetic", {}, {"vol_per_sec": 1.0})

    def test_risk_features_have_eight_ordered_values_and_default_behavior(self) -> None:
        self_obj = SimpleNamespace()
        defaults = self.risk_features(self_obj, {})
        self.assertEqual(defaults, [0.8, 0.0, 1.0, 0.0, 0.0, 0.2, 0.0, 1.0])

        kpi = {
            "paid_spread": 0.4,
            "exit_slip": 0.1,
            "ev_tick": 2.0,
            "rtt": 50.0,
            "streak": 3.0,
            "daily_pl": 25000.0,
            "tick_value": 5.0,
            "time_bucket": 2.5,
        }
        original = dict(kpi)
        values = self.risk_features(self_obj, kpi)

        self.assertEqual(values, [0.5, 2.0, 0.5, 1.0, 1.0, 1.0, 0.5, 1.0])
        self.assertEqual(len(values), 8)
        self.assertEqual(kpi, original)

    def test_risk_features_daily_pl_clamps_both_boundaries(self) -> None:
        self_obj = SimpleNamespace()
        for daily_pl, expected in (
            (10000.0, 1.0),
            (50000.0, 1.0),
            (-10000.0, -1.0),
            (-50000.0, -1.0),
        ):
            with self.subTest(daily_pl=daily_pl):
                values = self.risk_features(self_obj, {"daily_pl": daily_pl})
                self.assertEqual(values[4], expected)

    def test_strategy_size_preserves_original_mini_swing_minimum(self) -> None:
        self.assertEqual(self.strategy_size(50, "MINI_SWING"), 100)
        self.assertEqual(self.strategy_size(199, "MINI_SWING"), 100)
        self.assertEqual(self.strategy_size(202, "MINI_SWING"), 101)
        self.assertEqual(self.strategy_size(50, "OTHER"), 50)

    def test_realized_pnl_long_short_flat_and_invalid_entry_boundaries(self) -> None:
        pnl = self.realized_pnl

        self.assertAlmostEqual(pnl(SimpleNamespace(entry_price=100.0, position=1), 101.0), 1.0)
        self.assertAlmostEqual(pnl(SimpleNamespace(entry_price=100.0, position=1), 99.0), -1.0)
        self.assertAlmostEqual(pnl(SimpleNamespace(entry_price=100.0, position=-1), 99.0), 1.0)
        self.assertAlmostEqual(pnl(SimpleNamespace(entry_price=100.0, position=-1), 101.0), -1.0)
        self.assertEqual(pnl(SimpleNamespace(entry_price=100.0, position=0), 101.0), 0.0)
        self.assertEqual(pnl(SimpleNamespace(entry_price=0.0, position=1), 101.0), 0.0)
        self.assertEqual(pnl(SimpleNamespace(entry_price=1e-9, position=-1), 1.0), 0.0)

    @staticmethod
    def _router_context(**overrides: float) -> SimpleNamespace:
        values = {
            "heat": 0.7,
            "vwap_distance_pct": 0.2,
            "spread_ticks": 2.0,
            "vol_boost_ratio": 1.2,
            "market_temp": 0.6,
            "vol_rank": 0.5,
            "price_step": 2.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @staticmethod
    def _router(preferences: dict[str, float] | None = None,
                forbidden: dict[str, float] | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            prefer_swing_if=preferences or {},
            forbid_entry_if=forbidden or {},
        )

    @staticmethod
    def _swing_preferences() -> dict[str, float]:
        return {
            "heat": 0.7,
            "vwap_distance": 0.2,
            "spread_ticks": 2.0,
            "vol_boost_ratio": 1.2,
            "market_temp": 0.6,
        }

    def test_router_defaults_and_swing_gate_boundaries(self) -> None:
        self.assertEqual(
            self.router_decide(self._router(), self._router_context()), "MINI_SWING"
        )
        preferences = self._swing_preferences()
        self.assertEqual(
            self.router_decide(
                self._router(preferences), self._router_context()
            ),
            "SWING",
        )

        just_outside = (
            {"heat": 0.699},
            {"vwap_distance_pct": 0.201},
            {"spread_ticks": 2.001},
            {"vol_boost_ratio": 1.199},
            {"market_temp": 0.599},
        )
        for changed_value in just_outside:
            with self.subTest(changed_value=changed_value):
                self.assertEqual(
                    self.router_decide(
                        self._router(preferences), self._router_context(**changed_value)
                    ),
                    "MINI_SWING",
                )

    def test_router_forbidden_gate_boundaries_and_precedence(self) -> None:
        preferences = self._swing_preferences()
        forbidden = {"spread_ticks": 2.0, "vol_rank": 0.5, "price_step": 3.0}
        router = self._router(preferences, forbidden)

        self.assertEqual(self.router_decide(router, self._router_context()), "SWING")
        self.assertEqual(
            self.router_decide(router, self._router_context(spread_ticks=2.001)),
            "OBSERVE",
        )
        self.assertEqual(
            self.router_decide(router, self._router_context(vol_rank=0.499)),
            "OBSERVE",
        )
        self.assertEqual(
            self.router_decide(router, self._router_context(price_step=3.0)),
            "OBSERVE",
        )
        self.assertEqual(
            self.router_decide(router, self._router_context(price_step=2.999)),
            "SWING",
        )


if __name__ == "__main__":
    unittest.main()
