# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Literal, Dict, Any

Strategy = Literal["SCALP", "MINI_SWING", "OBSERVE"]

@dataclass
class Context:
    code: str
    price_step: int            # 1 or 5
    spread_ticks: float
    heat: float                # [0,1]（= OBIやエントリー信頼度の複合でもOK）
    obi: float                 # [0,1]（bid/(bid+ask)など）
    vwap_distance_pct: float   # 現値とVWAPの%乖離（無ければ0.0でも可）
    vol_boost_ratio: float     # 出来高加速(1min / 5minMA)
    vol_rank: float            # 日内ボリューム順位 [0,1]
    market_temp: float         # 市場温度 [0,1]

class StrategyRouter:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.pref = cfg.get("router",{}).get("prefer_swing_if", {
            "heat":0.72,"vwap_distance":0.25,"spread_ticks":2,"vol_boost_ratio":1.5,"market_temp":0.6
        })
        self.forbid = cfg.get("router",{}).get("forbid_entry_if", {
            "spread_ticks":6,"vol_rank":0.10,"price_step":5
        })

    def decide(self, cx: Context) -> Strategy:
        # 禁止（観測のみ）
        if cx.spread_ticks >= self.forbid["spread_ticks"]:
            return "OBSERVE"
        if cx.vol_rank <= self.forbid["vol_rank"]:
            return "OBSERVE"
        if cx.price_step == self.forbid["price_step"] and cx.spread_ticks > 2:
            return "OBSERVE"

        # MINI_SWING
        if (cx.heat >= self.pref["heat"]
            and cx.vwap_distance_pct <= self.pref["vwap_distance"]
            and cx.spread_ticks <= self.pref["spread_ticks"]
            and cx.vol_boost_ratio >= self.pref["vol_boost_ratio"]
            and cx.market_temp >= self.pref["market_temp"]):
            return "MINI_SWING"

        # SCALP
        if cx.heat >= 0.64 and cx.market_temp >= 0.40:
            return "SCALP"

        return "OBSERVE"
