from __future__ import annotations
import os, json, time
from typing import Dict, Any

try:
    import numpy as np
    from bandit_linucb import LinUCB
except Exception:
    LinUCB = None

RULE_CFG = {
    "size_levels": [100, 200, 300, 400],
    "rtx_ok_ms": 80,
    "friction_ok": 0.5,      # paid_spread + exit_slip
    "ev_tick_ok": 0.10,      # tick/本
    "drawdown_warn": -7000,
    "loss_streak_pause": 2,
    "min_gap_ms": 8000,
}

class RiskSizer:
    def __init__(self, model_path: str = "model_registry.json", use_bandit: bool = False):
        self.model_path = model_path
        self.use_bandit = use_bandit and (LinUCB is not None)
        self.last_decide_ms: Dict[str, int] = {}
        self.size_idx: Dict[str, int] = {}
        self._load()

    def _load(self):
        self.state = {}
        if os.path.exists(self.model_path):
            try:
                with open(self.model_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                self.state = obj.get("_risk_sizer_", {})
            except Exception:
                self.state = {}
        if self.use_bandit and LinUCB:
            bandit = self.state.get("bandit")
            if bandit:
                self.bandit = LinUCB.from_json(bandit)
            else:
                self.bandit = LinUCB(actions=["SIZE_100","SIZE_200","SIZE_300","SIZE_400"], d=8, alpha=0.4)
        else:
            self.bandit = None

    def _features(self, kpi: Dict[str, float]) -> list:
        ps = kpi.get("paid_spread", 0.5)
        es = kpi.get("exit_slip", 0.3)
        fr = ps + es
        ev = kpi.get("ev_tick", 0.0)
        rtt = kpi.get("rtt", 100.0)/100.0
        st = kpi.get("streak", 0)/3.0
        dp = max(-1.0, min(1.0, kpi.get("daily_pl", 0)/10000.0))
        tv = (kpi.get("tick_value", 1.0)/5.0)
        tb = kpi.get("time_bucket", 0)/5.0
        return [fr, ev, rtt, st, dp, tv, tb, 1.0]

    def decide(self, code: str, kpi: Dict[str, float]) -> Dict[str, Any]:
        now = int(time.time()*1000)
        if now - self.last_decide_ms.get(code, 0) < RULE_CFG["min_gap_ms"]:
            idx = self.size_idx.get(code, 0)
            return {"qty": RULE_CFG["size_levels"][idx], "reason": "MIN_GAP", "idx": idx}

        ps = kpi.get("paid_spread", 0.5); es = kpi.get("exit_slip", 0.3)
        fr = ps + es
        ev = kpi.get("ev_tick", 0.0)
        rtt = kpi.get("rtt", 100.0)
        streak = kpi.get("streak", 0)
        daily_pl = kpi.get("daily_pl", 0)

        idx = self.size_idx.get(code, 0)

        if daily_pl <= RULE_CFG["drawdown_warn"] or streak <= -RULE_CFG["loss_streak_pause"]:
            idx = max(0, idx-1); reason = "DRAWDOWN_OR_STREAK"
        elif fr > RULE_CFG["friction_ok"] or ev < RULE_CFG["ev_tick_ok"] or rtt > RULE_CFG["rtx_ok_ms"]:
            idx = max(0, idx-1); reason = "BAD_CONDITION"
        else:
            if fr <= RULE_CFG["friction_ok"] and ev >= RULE_CFG["ev_tick_ok"] and rtt <= RULE_CFG["rtx_ok_ms"]:
                idx = min(idx+1, len(RULE_CFG["size_levels"])-1); reason = "GOOD_CONDITION"
            else:
                reason = "HOLD_SIZE"

        self.size_idx[code] = idx
        self.last_decide_ms[code] = now
        qty = RULE_CFG["size_levels"][idx]
        return {"qty": qty, "reason": reason, "idx": idx}
    def size_for_strategy(base_qty: int, strategy: str) -> int:
        if strategy == "MINI_SWING":
            return max(100, int(base_qty * 0.5))
        return int(base_qty)