# -*- coding: utf-8 -*-
import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces

class TradingEnv(gym.Env):
    """
    Audit対応版: 純粋利益追求型 AIトレード環境 (v7.2 Strict)
    - ランダムスタート導入
    - 30分強制決済 (ペナルティ強化版)
    - 報酬クリッピング
    """
    def __init__(self, df, stock_code="UNKNOWN"):
        super(TradingEnv, self).__init__()
        self.df = df.reset_index(drop=True)
        self.MAX_STEPS = len(self.df) - 1
        self.stock_code = str(stock_code)

        # ==========================================
        # ★ 銘柄分類リスト
        # ==========================================
        self.TYPE_SCALP = ["9501", "285A"]
        self.TYPE_SWING = ["6920", "6146", "7014", "7011", "7003", "6954", "7203", "8058", "8031", "5803", "7974"]

        # === 性格設定 ===
        if self.stock_code in self.TYPE_SCALP:
            self.COST_PENALTY = 0.05      # 参加費
            self.PROFIT_MULTIPLIER = 1.0  
        elif self.stock_code in self.TYPE_SWING:
            self.COST_PENALTY = 0.1       
            self.PROFIT_MULTIPLIER = 1.5  
        else:
            self.COST_PENALTY = 0.08
            self.PROFIT_MULTIPLIER = 1.2

        # 【行動空間】 0:待機, 1:買い, 2:決済
        self.action_space = spaces.Discrete(3)

        # 【観測空間】 (15次元)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(15,), dtype=np.float32
        )

    def reset(self, seed=None, options=None):
        # Seedの固定
        super().reset(seed=seed)
        
        # ランダムスタート (過学習防止)
        limit = len(self.df) - 1000
        if limit > 60:
            self.current_step = self.np_random.integers(60, limit)
        else:
            self.current_step = 60

        self.position = 0      # 0:ノーポジ, 1:ロング
        self.entry_price = 0.0
        self.balance = 0.0
        self.trades = []
        self.hold_duration = 0
        
        return self._next_observation(), {}

    def _next_observation(self):
        row = self.df.iloc[self.current_step]
        prev_row = self.df.iloc[self.current_step - 1]
        
        # 1. 価格変化率
        prev_close = float(prev_row['close'])
        pct_change = 0.0
        if abs(prev_close) > 1e-9:
            pct_change = (float(row['close']) - prev_close) / prev_close * 100

        # 2. 含み損益率
        unrealized_pnl_pct = 0.0
        if self.position == 1:
            ep = float(self.entry_price)
            if abs(ep) > 1e-9:
                unrealized_pnl_pct = (float(row['close']) - ep) / ep * 100

        # 指標取得
        obi = row.get('obi', 0.5)
        macd_hist = row.get('MACDh_12_26_9', 0.0)
        bb_p = row.get('BBP_20_2.0', 0.5)
        rci9 = row.get('rci_9', 0.0)
        rci26 = row.get('rci_26', 0.0)

        # PSAR乖離
        psar_l = row.get('PSARl_0.02_0.2', np.nan)
        psar_s = row.get('PSARs_0.02_0.2', np.nan)
        sar_val = psar_l if not np.isnan(psar_l) else (psar_s if not np.isnan(psar_s) else float(row['close']))
        sar_dist = 0.0
        if abs(sar_val) > 1e-9:
            sar_dist = (float(row['close']) - sar_val) / sar_val * 100

        rsi = row.get('RSI_14', 50.0)
        vol_ratio = row.get('vol_ratio', 1.0)
        sma25 = row.get('SMA_25', float(row['close']))
        dist_sma25 = 0.0
        if abs(sma25) > 1e-9:
            dist_sma25 = (float(row['close']) - sma25) / sma25 * 100

        dist_ma15 = row.get('dist_ma15', 0.0) * 100
        volatility = row.get('volatility', 0.0) * 100

        # 観測データ
        obs = np.array([
            pct_change, obi, macd_hist, bb_p, rci9 / 100.0, rci26 / 100.0,
            sar_dist, rsi / 100.0, vol_ratio, dist_sma25, float(self.position),
            unrealized_pnl_pct, dist_ma15, volatility, 0.0
        ], dtype=np.float32)
        
        return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)

    def step(self, action):
        current_price = float(self.df.iloc[self.current_step]['close'])
        reward = 0.0
        terminated = False
        truncated = False
        
        # --- ポジション保有中の評価 ---
        if self.position == 1:
            prev_price = float(self.df.iloc[self.current_step - 1]['close'])
            price_diff = current_price - prev_price
            
            # 値動き自体を報酬にする
            reward += price_diff * 0.01 
            self.hold_duration += 1

            # ★修正: 時間切れ強制決済 (ペナルティ強化)
            if self.hold_duration >= 30:
                action = 2 # 強制EXIT扱いにする
                # ペナルティを -0.1 から -1.0 に強化 (利確1回分が吹き飛ぶ重罪)
                reward -= 1.0 

        # --- 行動判定 ---
        if action == 1: # ENTRY
            if self.position == 0:
                self.position = 1
                self.entry_price = current_price
                self.hold_duration = 0
                reward -= self.COST_PENALTY 
            else:
                reward -= 0.01

        elif action == 2: # EXIT
            if self.position == 1:
                profit = current_price - self.entry_price
                self.balance += profit
                self.trades.append(profit)
                
                if profit > 0:
                    reward += (profit * 0.01) * self.PROFIT_MULTIPLIER
                    reward += 1.0 # 利確ボーナス
                else:
                    reward += (profit * 0.015) - 0.5 # 損切りペナルティ
                
                self.position = 0
                self.entry_price = 0.0
                self.hold_duration = 0
            else:
                reward -= 0.01 

        elif action == 0: # HOLD
            pass

        self.current_step += 1
        if self.current_step >= self.MAX_STEPS:
            terminated = True
        
        # 報酬クリッピング
        reward = np.clip(reward, -10.0, 10.0)

        info = {"balance": self.balance, "trades": len(self.trades)}
        return self._next_observation(), float(reward), terminated, truncated, info