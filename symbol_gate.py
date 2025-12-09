from __future__ import annotations
import time
from typing import Dict, Any

RULES = {
    "spread_max": 1.0,
    "vol5_min": 6000,
    "best_qty_min": 300,
    "best_qty_ratio": 3.0,
    "rtt_max_ms": 80,
    "min_gap_ms": 8000,
}

class SymbolGate:
    def __init__(self, model_path: str = "model_registry.json"):
        self.model_path = model_path
        self.last_decide_ms: Dict[str,int] = {}

    def decide(self, ctx, qty: int) -> Dict[str,Any]:
        now = int(time.time()*1000)
        if now - self.last_decide_ms.get(ctx.code, 0) < RULES["min_gap_ms"]:
            return {"allow_5yen": True, "reason": "MIN_GAP"}

        tv = getattr(ctx, "tick_value", 1.0)
        if tv < 5.0:
            self.last_decide_ms[ctx.code] = now
            return {"allow_5yen": True, "reason": "TICK_1YEN"}

        fr = getattr(ctx, "spread_ticks", 1.0)
        v5 = getattr(ctx, "vol5", 0.0)
        rtt = getattr(ctx, "board_rtt_ms", 100.0)
        bq = getattr(ctx, "best_bid_qty", 0.0)

        if fr <= RULES["spread_max"] and v5 >= RULES["vol5_min"] and rtt <= RULES["rtt_max_ms"] and bq >= max(RULES["best_qty_min"], qty*RULES["best_qty_ratio"]):
            allow = True; reason = "GOOD_LIQUIDITY"
        else:
            allow = False; reason = "BLOCK_CONDITION"

        self.last_decide_ms[ctx.code] = now
        return {"allow_5yen": allow, "reason": reason}