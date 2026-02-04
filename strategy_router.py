# -*- coding: utf-8 -*-
"""
StrategyRouter v22.9 (Full Compatibility Edition)
=================================================
【v22.9 修正点】
- Context: 全引数をオプショナルにして controller.py v22.6 と完全互換
- StrategyRouter: decide() メソッドを追加して旧APIとの互換性を確保
"""

import numpy as np
from typing import Any, Tuple, Dict, Optional
import time


class Context:
    """
    AI判断に必要な現場のデータ構造体
    v22.9: 全引数をオプショナルにして完全互換
    """
    def __init__(
        self, 
        code: str = "",
        # 新API用（オプショナル）
        current_price: float = 0.0, 
        position: Optional[Dict[str, Any]] = None, 
        collector: Any = None, 
        env_stats: Any = None, 
        # 旧API用（オプショナル）
        price_step: float = 1.0,
        spread_ticks: float = 0.0,
        heat: float = 0.0,
        obi: float = 0.5,
        vwap_distance_pct: float = 0.0,
        vol_boost_ratio: float = 1.0,
        vol_rank: float = 0.5,
        market_temp: float = 0.5,
        **kwargs  # 未知の引数を吸収
    ):
        self.code = code
        self.current_price = current_price
        self.position = position or {}
        self.collector = collector
        self.env_stats = env_stats
        self.price_step = price_step
        self.spread_ticks = spread_ticks
        self.heat = heat
        self.obi = obi
        self.vwap_distance_pct = vwap_distance_pct
        self.vol_boost_ratio = vol_boost_ratio
        self.vol_rank = vol_rank
        self.market_temp = market_temp
        
        # 将来のための予備ポケット
        self.extras = kwargs


class StrategyRouter:
    """
    AIモデル(Brain)と現場データ(Context)を仲介するルーター
    v22.9: 新旧両方のAPIに対応
    """
    def __init__(self, config: dict = None, broker=None, model=None):
        """
        初期化
        - 旧API: StrategyRouter({"router": {...}})
        - 新API: StrategyRouter(code, broker, model)
        """
        # configが文字列の場合は旧APIのcode引数として扱う
        if isinstance(config, str):
            self.code = config
            self.broker = broker
            self.model = model
            self.config = {}
        else:
            self.code = ""
            self.broker = broker
            self.model = model
            self.config = config or {}
        
        self.prev_close = 0.0
        
        # 旧API用の設定を読み込み
        router_cfg = self.config.get("router", {})
        self.prefer_swing_if = router_cfg.get("prefer_swing_if", {})
        self.forbid_entry_if = router_cfg.get("forbid_entry_if", {})

    def decide(self, ctx: Context) -> str:
        """
        旧API: 戦略を決定する
        Returns: "SWING", "SCALP", "MINI_SWING", "OBSERVE"
        """
        # エントリー禁止条件をチェック
        forbid = self.forbid_entry_if
        if forbid:
            if ctx.spread_ticks > forbid.get("spread_ticks", 999):
                return "OBSERVE"
            if ctx.vol_rank < forbid.get("vol_rank", 0):
                return "OBSERVE"
            if ctx.price_step >= forbid.get("price_step", 999):
                return "OBSERVE"
        
        # スイング条件をチェック
        pref = self.prefer_swing_if
        if pref:
            heat_ok = ctx.heat >= pref.get("heat", 0)
            vwap_ok = ctx.vwap_distance_pct <= pref.get("vwap_distance", 999)
            spread_ok = ctx.spread_ticks <= pref.get("spread_ticks", 999)
            vol_ok = ctx.vol_boost_ratio >= pref.get("vol_boost_ratio", 0)
            temp_ok = ctx.market_temp >= pref.get("market_temp", 0)
            
            if heat_ok and vwap_ok and spread_ok and vol_ok and temp_ok:
                return "SWING"
        
        # デフォルトは MINI_SWING
        return "MINI_SWING"

    def predict(self, ctx: Context) -> Tuple[int, float, list]:
        """
        新API: AIに状況を伝えて、行動を決定させる
        Returns: (action, confidence, raw_obs)
        """
        obs = self._create_observation(ctx)

        if self.model is None:
            return 0, 0.0, obs

        try:
            action, _states = self.model.predict(obs, deterministic=True)
        except Exception:
            return 0, 0.0, obs
        
        confidence = 0.85 
        return int(action), confidence, obs

    def _create_observation(self, ctx: Context):
        """
        AIへの入力データ作成 (18次元 - v22.5仕様)
        """
        pct_change = 0.0
        if self.prev_close > 0:
            pct_change = (ctx.current_price - self.prev_close) / self.prev_close * 100.0
        self.prev_close = ctx.current_price

        # Collectorからデータ取得
        latest = {}
        bd = {}
        if ctx.collector:
            latest = getattr(ctx.collector, 'latest_data', {})
            bd = getattr(ctx.collector, 'latest_board', {})

        ma15 = latest.get('ma15', ctx.current_price)
        ma75 = latest.get('ma75', ctx.current_price)
        vwap = latest.get('vwap', ctx.current_price)
        
        dist_ma15 = (ctx.current_price - ma15) / (ma15 + 1e-9) * 100 if ma15 else 0
        ma75_dist = (ctx.current_price - ma75) / (ma75 + 1e-9) * 100 if ma75 else 0
        vwap_dist = (ctx.current_price - vwap) / (vwap + 1e-9) * 100 if vwap else 0

        obi = bd.get('obi', ctx.obi)
        obi_change = bd.get('obi_change', 0.0)
        sell_pressure = bd.get('sell_pressure', 0.0) / 100.0 
        buy_pressure = bd.get('buy_pressure', 0.0) / 100.0
        ndbi = bd.get('ndbi', 0.0)

        rsi = latest.get('rsi_14', 50.0) / 100.0
        rci = latest.get('rci_9', 0.0) / 100.0
        vol_ratio = latest.get('vol_ratio', ctx.vol_boost_ratio)
        volatility = latest.get('volatility', 0.0)
        
        mtf_trend = 0.0
        if ctx.current_price > 0:
            if ctx.current_price > ma75 and ctx.current_price > vwap:
                mtf_trend = 1.0
            elif ctx.current_price < ma75 and ctx.current_price < vwap:
                mtf_trend = -1.0

        pos_val = 0.0
        pnl_val = 0.0
        time_val = 0.0
        max_profit_val = 0.0

        if ctx.position:
            side_str = ctx.position.get("side", "BUY")
            side = 1.0 if side_str == "BUY" else -1.0
            entry = float(ctx.position.get("entry", ctx.current_price))
            
            if entry > 0 and ctx.current_price > 0:
                if side == 1.0:
                    pnl_pct = (ctx.current_price - entry) / entry * 100
                else:
                    pnl_pct = (entry - ctx.current_price) / entry * 100
            else:
                pnl_pct = 0.0
            
            elapsed = time.time() - ctx.position.get("time", ctx.position.get("ts", time.time()))
            max_hold_sec = 1800 
            
            pos_val = side
            pnl_val = pnl_pct
            time_val = min(1.0, elapsed / max_hold_sec)
            max_profit_val = max(0.0, pnl_pct)

        obs = np.array([
            np.clip(pct_change, -10, 10),
            np.clip(vwap_dist, -5, 5),
            np.clip(ma75_dist, -5, 5),
            np.clip(dist_ma15, -10, 10),
            
            np.clip(obi, 0, 1),
            np.clip(obi_change, -0.5, 0.5),
            np.clip(sell_pressure, 0, 10),
            np.clip(buy_pressure, 0, 10),
            np.clip(ndbi, -1, 1),

            np.clip(rsi, 0, 1),
            np.clip(rci, -1, 1),
            np.clip(vol_ratio, 0, 5),
            np.clip(volatility, 0, 10),
            mtf_trend,

            pos_val,
            np.clip(pnl_val, -5, 5),
            time_val,
            max_profit_val
        ], dtype=np.float32)

        return np.nan_to_num(obs, nan=0.0)