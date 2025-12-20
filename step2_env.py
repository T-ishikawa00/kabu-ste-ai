# -*- coding: utf-8 -*-
import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces

class TradingEnv(gym.Env):
    """
    北電特化型：AIトレード練習用環境 (v4 超積極型)
    """
    def __init__(self, df):
        super(TradingEnv, self).__init__()
        self.df = df.reset_index(drop=True)
        self.MAX_STEPS = len(self.df) - 1
        
        # --- 設定エリア ---
        # 手数料は完全ゼロ（AIを自由にさせるため）
        self.COMMISSION_PCT = 0.0 
        
        # 【行動空間】 0:待機(HOLD), 1:新規買い(ENTRY), 2:決済(EXIT)
        self.action_space = spaces.Discrete(3)

        # 【観測空間】
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(7,), dtype=np.float32
        )

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        
        self.current_step = 20
        self.position = 0      # 0:ノーポジ, 1:ロング
        self.entry_price = 0.0
        self.balance = 0.0
        self.trades = []
        
        return self._next_observation(), {}

    def _next_observation(self):
        row = self.df.iloc[self.current_step]
        prev_row = self.df.iloc[self.current_step - 1]
        
        unrealized_pnl_pct = 0.0
        if self.position == 1:
            unrealized_pnl_pct = (row['close'] - self.entry_price) / self.entry_price * 100

        obs = np.array([
            (row['close'] - prev_row['close']) / prev_row['close'] * 100, # 価格変化率
            row['dist_ma15'] * 100,  # MA乖離
            row['volatility'] * 100, # ボラティリティ
            row['vol_ratio'],        # 出来高倍率
            row['rsi'] / 100.0,      # RSI
            float(self.position),    # ポジション
            unrealized_pnl_pct       # 含み損益率
        ], dtype=np.float32)
        
        return np.nan_to_num(obs)

    def step(self, action):
        # 現在の足と前の足
        current_price = self.df.iloc[self.current_step]['close']
        prev_price = self.df.iloc[self.current_step - 1]['close']
        
        reward = 0.0
        terminated = False
        truncated = False
        
        # ★★★ ここが修正ポイント（Dense Reward） ★★★
        # 決済しなくても、持っているだけで「値上がり」すれば報酬を与える！
        if self.position == 1:
            # (今の価格 - 前の価格)
            price_diff = current_price - prev_price
            # 値幅そのものを報酬に足す（感度を上げるため10倍などしても良いが、まずは素直に）
            reward += price_diff
            
        # --- 行動の判定 ---
        
        # 1: 新規買い (ENTRY)
        if action == 1:
            if self.position == 0:
                self.position = 1
                self.entry_price = current_price
                # エントリーボーナス（背中を押す）
                reward += 0.1
            else:
                # 既に持っているのに買うのは無駄
                reward -= 0.1

        # 2: 決済 (EXIT)
        elif action == 2:
            if self.position == 1:
                profit = current_price - self.entry_price
                self.balance += profit
                self.trades.append(profit)
                
                # 利確ボーナス（勝ち逃げは偉い！）
                if profit > 0:
                    reward += 10.0
                else:
                    reward -= 5.0
                
                self.position = 0
                self.entry_price = 0.0
            else:
                # 持ってないのに売るのは無駄
                reward -= 0.1
        
        # 0: 待機 (HOLD/WAIT)
        elif action == 0:
            if self.position == 0:
                # ノーポジでぼーっとしてるのは「機会損失」としてわずかに罰する
                reward -= 0.01

        # ステップ進行
        self.current_step += 1
        if self.current_step >= self.MAX_STEPS:
            terminated = True
        
        info = {
            "balance": self.balance,
            "trades": len(self.trades)
        }

        return self._next_observation(), float(reward), terminated, truncated, info