# -*- coding: utf-8 -*-
from typing import Dict, Any

class MarketTemperature:
    """
    直近5分の特徴量から「市場温度」を 0〜1 で計算する。
    - 出来高ペース
    - 板厚
    - スプレッド
    を単純に正規化して合成する簡易版。
    """

    def __init__(self,
                 vol_hot: float = 500.0,
                 vol_cold: float = 50.0,
                 depth_hot: float = 500.0,
                 depth_cold: float = 50.0,
                 spread_hot: float = 1.0,
                 spread_cold: float = 5.0):
        self.vol_hot = vol_hot
        self.vol_cold = vol_cold
        self.depth_hot = depth_hot
        self.depth_cold = depth_cold
        self.spread_hot = spread_hot
        self.spread_cold = spread_cold

    def _scale01(self, x: float, cold: float, hot: float, invert: bool = False) -> float:
        if hot == cold:
            return 0.0
        v = (x - cold) / (hot - cold)
        v = max(0.0, min(1.0, v))
        if invert:
            v = 1.0 - v
        return v

    def compute(self,
                code: str,
                tick: Dict[str, Any],
                stm_feats: Dict[str, float]) -> Dict[str, float]:
        vol_score = self._scale01(stm_feats["vol_per_sec"], self.vol_cold, self.vol_hot)
        depth_avg = (stm_feats["bid_avg"] + stm_feats["ask_avg"]) / 2.0
        depth_score = self._scale01(depth_avg, self.depth_cold, self.depth_hot)
        spread_score = self._scale01(stm_feats["spread_avg"], self.spread_hot, self.spread_cold, invert=True)

        # シンプルに平均（必要なら重み付け）
        market_heat = (vol_score + depth_score + spread_score) / 3.0

        return {
            "market_heat": market_heat,
            "vol_score": vol_score,
            "depth_score": depth_score,
            "spread_score": spread_score,
        }
