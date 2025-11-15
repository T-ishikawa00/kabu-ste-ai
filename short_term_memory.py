# -*- coding: utf-8 -*-
import datetime
from collections import deque
from typing import Deque, Dict, Any, Optional

class ShortTermMemory:
    """
    各銘柄ごとに「直近 lookback_sec 秒」の簡易統計を保持する。
    - 価格変化率
    - 出来高ペース
    - 板厚（bid/ask）
    などをまとめて Entry/Exit に渡す。
    """

    def __init__(self, lookback_sec: int = 300):
        self.lookback_sec = lookback_sec
        # code -> deque[dict(ts, price, volume, bid_size, ask_size)]
        self.buffers: Dict[str, Deque[Dict[str, Any]]] = {}

    def update(self, code: str, tick: Dict[str, Any]):
        """
        tick 例:
        {
          "ts": datetime.datetime,
          "price": float,
          "volume": int,
          "bid_size": int,
          "ask_size": int,
          "spread_ticks": int,
        }
        """
        buf = self.buffers.setdefault(code, deque())
        buf.append(tick)

        cutoff = tick["ts"] - datetime.timedelta(seconds=self.lookback_sec)
        while buf and buf[0]["ts"] < cutoff:
            buf.popleft()

    def get_features(self, code: str) -> Dict[str, float]:
        buf = self.buffers.get(code)
        if not buf or len(buf) < 2:
            return {
                "ret_5min": 0.0,
                "vol_per_sec": 0.0,
                "bid_avg": 0.0,
                "ask_avg": 0.0,
                "spread_avg": 0.0,
            }

        prices = [b["price"] for b in buf]
        volumes = [b["volume"] for b in buf]
        bid_sizes = [b["bid_size"] for b in buf]
        ask_sizes = [b["ask_size"] for b in buf]
        spreads = [b["spread_ticks"] for b in buf]

        dt = (buf[-1]["ts"] - buf[0]["ts"]).total_seconds()
        if dt <= 0:
            dt = 1.0

        ret_5min = (prices[-1] - prices[0]) / prices[0] if prices[0] > 0 else 0.0
        vol_per_sec = sum(volumes) / dt

        return {
            "ret_5min": ret_5min,
            "vol_per_sec": vol_per_sec,
            "bid_avg": sum(bid_sizes) / len(bid_sizes),
            "ask_avg": sum(ask_sizes) / len(ask_sizes),
            "spread_avg": sum(spreads) / len(spreads),
        }
