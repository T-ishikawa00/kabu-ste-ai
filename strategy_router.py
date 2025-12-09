# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Literal, Dict, Any

Strategy = Literal["MINI_SWING", "OBSERVE"]  # ★ SCALPは封印

@dataclass
class Context:
    code: str
    price_step: int            # 1 or 5
    spread_ticks: float
    heat: float                # [0,1]
    obi: float                 # [0,1]
    vwap_distance_pct: float   # 現値とVWAPの%乖離
    vol_boost_ratio: float     # 出来高加速(1min / 5minMA)
    vol_rank: float            # 日内ボリューム順位 [0,1]
    market_temp: float         # 市場温度 [0,1]

class StrategyRouter:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.pref = cfg.get("router",{}).get("prefer_swing_if", {
            "heat":0.70,
            "vwap_distance":0.35,
            "spread_ticks":2,
            "vol_boost_ratio":1.2,
            "market_temp":0.60,
        })
        self.forbid = cfg.get("router",{}).get("forbid_entry_if", {
            "spread_ticks":6,
            "vol_rank":0.10,
            "price_step":5,
        })

    def decide(self, cx: Context) -> Strategy:
        # ===== 禁止ゾーン：薄すぎ・過疎すぎ・5円で広すぎ → 触らない =====
        if cx.spread_ticks >= self.forbid["spread_ticks"]:
            return "OBSERVE"
        if cx.vol_rank <= self.forbid["vol_rank"]:
            return "OBSERVE"
        if cx.price_step == self.forbid["price_step"] and cx.spread_ticks > 2:
            return "OBSERVE"

        # ===== ミニスイング条件：本当に“熱い”ときだけ =====
        if (
            cx.heat >= self.pref["heat"]
            and cx.vwap_distance_pct <= self.pref["vwap_distance"]
            and cx.spread_ticks <= self.pref["spread_ticks"]
            and cx.vol_boost_ratio >= self.pref["vol_boost_ratio"]
            and cx.market_temp >= self.pref["market_temp"]
        ):
            return "MINI_SWING"

        # それ以外は全部“観測のみ”
        return "OBSERVE"

