# -*- coding: utf-8 -*-
import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces

class TradingEnv(gym.Env):
    """
    北電特化型：AIトレード練習用環境 (v6 師匠対応・完全ハイブリッド版)
    """
    def __init__(self, df):
        super(TradingEnv, self).__init__()
        self.df = df.reset_index(drop=True)
        self.MAX_STEPS = len(self.df) - 1
        
        # 手数料は完全ゼロ
        self.COMMISSION_PCT = 0.0 
        
        # 【行動空間】 0:待機, 1:買い, 2:決済
        self.action_space = spaces.Discrete(3)

        # 【観測空間】
        # 師匠たちの指標(RCI, Parabolic)も含めるため shape=(15,) に拡張
        # 1.変化率 2.OBI 3.MACDヒスト 4.BB%B 5.RCI短期 6.RCI中期 
        # 7.PSAR乖離 8.RSI 9.出来高倍率 10.SMA25乖離 
        # 11.ポジ 12.含み損益 13.旧MA乖離 14.ボラ 15.予備
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(15,), dtype=np.float32
        )

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        
        # RCI(52期間)などの計算のため、スタート位置を少し遅らせる
        self.current_step = 60 
        self.position = 0      # 0:ノーポジ, 1:ロング
        self.entry_price = 0.0
        self.balance = 0.0
        self.trades = []
        
        return self._next_observation(), {}

    def _next_observation(self):
        # データ取得
        row = self.df.iloc[self.current_step]
        prev_row = self.df.iloc[self.current_step - 1]
        
        # 1. 価格変化率 (Change %)
        prev_close = float(prev_row['close'])
        pct_change = 0.0
        if abs(prev_close) > 1e-9:
            pct_change = (float(row['close']) - prev_close) / prev_close * 100

        # 2. 含み損益率 (Unrealized PnL %)
        unrealized_pnl_pct = 0.0
        if self.position == 1:
            ep = float(self.entry_price)
            if abs(ep) > 1e-9:
                unrealized_pnl_pct = (float(row['close']) - ep) / ep * 100

        # --- 新指標の取得 (step3_train.py で計算済み) ---
        # カラムがない場合に備えて .get() で安全に取得
        
        # 板気配 (OBI)
        obi = row.get('obi', 0.5)

        # MACD ヒストグラム (トレンドの強弱)
        macd_hist = row.get('MACDh_12_26_9', 0.0)

        # ボリンジャーバンド %B (1.0以上で突き抜け)
        bb_p = row.get('BBP_20_2.0', 0.5)

        # RCI (順位相関指数) - 師匠のイチオシ
        rci9 = row.get('rci_9', 0.0)
        rci26 = row.get('rci_26', 0.0)

        # パラボリック (SAR) との乖離
        # PSARl(ロング用)とPSARs(ショート用)を統合
        psar_l = row.get('PSARl_0.02_0.2', np.nan)
        psar_s = row.get('PSARs_0.02_0.2', np.nan)
        sar_val = psar_l if not np.isnan(psar_l) else (psar_s if not np.isnan(psar_s) else float(row['close']))
        
        sar_dist = 0.0
        if abs(sar_val) > 1e-9:
            sar_dist = (float(row['close']) - sar_val) / sar_val * 100

        # RSI (過熱感)
        rsi = row.get('RSI_14', 50.0)

        # 出来高倍率 (Vol Ratio)
        vol_ratio = row.get('vol_ratio', 1.0)
        
        # SMA25乖離 (短期トレンド)
        sma25 = row.get('SMA_25', float(row['close']))
        dist_sma25 = 0.0
        if abs(sma25) > 1e-9:
            dist_sma25 = (float(row['close']) - sma25) / sma25 * 100

        # 旧指標 (一応残しておく)
        dist_ma15 = row.get('dist_ma15', 0.0) * 100
        volatility = row.get('volatility', 0.0) * 100

        # --- 観測データの作成 (15個) ---
        obs = np.array([
            pct_change,          # 1. 変化率
            obi,                 # 2. 板気配 (0~1)
            macd_hist,           # 3. MACDヒスト
            bb_p,                # 4. BB %B
            rci9 / 100.0,        # 5. RCI短期 (-1.0 ~ 1.0)
            rci26 / 100.0,       # 6. RCI中期
            sar_dist,            # 7. PSAR乖離
            rsi / 100.0,         # 8. RSI
            vol_ratio,           # 9. 出来高倍率
            dist_sma25,          # 10. SMA25乖離
            float(self.position),# 11. ポジション
            unrealized_pnl_pct,  # 12. 含み損益
            dist_ma15,           # 13. 旧MA乖離
            volatility,          # 14. ボラティリティ
            0.0                  # 15. 予備 (将来用)
        ], dtype=np.float32)
        
        # NaN/Inf対策 (最強の防具)
        return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)

    def step(self, action):
        current_price = float(self.df.iloc[self.current_step]['close'])
        prev_price = float(self.df.iloc[self.current_step - 1]['close'])
        
        reward = 0.0
        terminated = False
        truncated = False
        
        # 資産カーブボーナス (持っているだけで値上がりすれば報酬)
        if self.position == 1:
            reward += (current_price - prev_price)

        # --- 行動判定 ---
        if action == 1: # ENTRY
            if self.position == 0:
                self.position = 1
                self.entry_price = current_price
                reward += 0.1 # エントリー自体への微小報酬
            else:
                reward -= 0.1 # 無駄エントリー罰則

        elif action == 2: # EXIT
            if self.position == 1:
                profit = current_price - self.entry_price
                self.balance += profit
                self.trades.append(profit)
                if profit > 0: reward += 10.0 # 勝利ボーナス
                else: reward -= 5.0           # 敗北ペナルティ
                self.position = 0
                self.entry_price = 0.0
            else:
                reward -= 0.1 # 無駄エグジット罰則

        elif action == 0: # HOLD
            if self.position == 0:
                reward -= 0.01 # 機会損失ペナルティ

        self.current_step += 1
        if self.current_step >= self.MAX_STEPS:
            terminated = True
        
        info = {"balance": self.balance, "trades": len(self.trades)}
        return self._next_observation(), float(reward), terminated, truncated, info