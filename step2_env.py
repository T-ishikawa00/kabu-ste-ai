# -*- coding: utf-8 -*-
"""
TradingEnv v12.0 (Short Selling Supported)
======================================================
v12.0 変更点:
- 空売り（Short）対応
- 行動定義を「強気(1)/弱気(2)」に変更
- 下落トレンドでの収益化を学習
- 新ポートフォリオ（重工・半導体・海運）に対応した性格分類
"""

import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces

class TradingEnv(gym.Env):
    """
    空売り対応・勝率重視のトレード環境
    Action:
        0: HOLD (待機)
        1: BULL (強気) -> 新規買い / 空売り決済
        2: BEAR (弱気) -> 新規空売り / 買い決済
    Position:
        0: なし
        1: 買い (Long)
       -1: 売り (Short)
    """
    
    def __init__(self, df, stock_code="UNKNOWN"):
        super(TradingEnv, self).__init__()
        self.df = df.reset_index(drop=True)
        self.MAX_STEPS = len(self.df) - 1
        self.stock_code = str(stock_code)

        # ==========================================
        # ★銘柄性格分類 (v12.0 Aggressive Portfolio 対応)
        # ==========================================
        # 安定・大型・ディフェンシブ（ボラティリティ低め）
        self.TYPE_STABLE = [
            "9501", # 東電
            "8411", # みずほ
            "6954", # ファナック
            "6506", # 安川電機
            "8136", # サンリオ
            "7974"  # 任天堂
        ]

        # 暴れ馬・トレンド・半導体（ボラティリティ高め）
        # ※ここに入っている銘柄は、利食い幅などを広めに取る調整が入る場合があります
        self.TYPE_VOLATILE = [
            "7011", "7013", "7003", "6269", # 重工・造船
            "285A", "3110",                 # 半導体 (キオクシア等)
            "9984",                         # SBG
            "9107",                         # 川崎汽船
            "6920", "6146",                 # レーザー・ディスコ (監視用データ学習時)
            "GENERAL"                       # 汎用モデル
        ]
        
        # デフォルトパラメータ
        self.ENTRY_COST = 0.05
        self.WIN_REWARD = 1.5
        self.LOSE_PENALTY = 1.5
        self.MAX_HOLD = 60

        # 行動空間: 0=待機, 1=強気(買/解), 2=弱気(売/解)
        self.action_space = spaces.Discrete(3)

        # 観測空間: 17次元
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(17,), dtype=np.float32
        )

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        min_start = 60
        max_start = max(min_start + 1, len(self.df) - 500)
        self.current_step = self.np_random.integers(min_start, max_start) if max_start > min_start else min_start

        self.position = 0  # 0:None, 1:Long, -1:Short
        self.entry_price = 0.0
        self.balance = 0.0
        self.trades = []
        self.hold_duration = 0
        self.max_profit_pct = 0.0
        
        self.episode_wins = 0
        self.episode_losses = 0
        
        return self._next_observation(), {}

    def _next_observation(self):
        row = self.df.iloc[self.current_step]
        prev = self.df.iloc[self.current_step - 1]
        cur_price = float(row['close'])
        
        # 変化率
        pct_change = (cur_price - float(prev['close'])) / float(prev['close']) * 100 if prev['close'] else 0

        # 含み損益% (Long/Short対応)
        unrealized_pnl = 0.0
        if self.position == 1: # Long
            unrealized_pnl = (cur_price - self.entry_price) / self.entry_price * 100
        elif self.position == -1: # Short
            unrealized_pnl = (self.entry_price - cur_price) / self.entry_price * 100

        # 特徴量抽出 (欠損値ケア)
        def get_val(key, default=0.0):
            return float(row.get(key, default))

        obs = np.array([
            pct_change,
            get_val('obi', 0.5),
            get_val('MACDh_12_26_9', 0.0),
            get_val('BBP_20_2.0', 0.5),
            get_val('rci_9', 0.0) / 100.0,
            get_val('rci_26', 0.0) / 100.0,
            0.0, # SAR dist (省略)
            get_val('RSI_14', 50.0) / 100.0,
            get_val('vol_ratio', 1.0),
            get_val('dist_sma25', 0.0),
            float(self.position), # -1, 0, 1
            unrealized_pnl,
            get_val('dist_ma15', 0.0),
            get_val('volatility', 0.0),
            self.hold_duration / self.MAX_HOLD,
            get_val('ndbi', 0.0),
            get_val('hsr', 0.0),
        ], dtype=np.float32)
        
        return np.nan_to_num(obs)

    def step(self, action):
        row = self.df.iloc[self.current_step]
        cur_price = float(row['close'])
        reward = 0.0
        terminated = False
        
        # --- ポジション保有中の処理 ---
        if self.position != 0:
            self.hold_duration += 1
            
            # 含み益更新
            pnl_pct = 0.0
            if self.position == 1:
                pnl_pct = (cur_price - self.entry_price) / self.entry_price * 100
            elif self.position == -1:
                pnl_pct = (self.entry_price - cur_price) / self.entry_price * 100
                
            self.max_profit_pct = max(self.max_profit_pct, pnl_pct)

            # 強制決済（時間切れ）
            if self.hold_duration >= self.MAX_HOLD:
                if self.position == 1: action = 2 # Long -> Sell
                elif self.position == -1: action = 1 # Short -> Cover
                reward -= 0.5

        # --- 行動判定 (BULL/BEARロジック) ---
        
        # Action 1: BULL (強気)
        if action == 1:
            if self.position == 0:
                # 新規買い
                self.position = 1
                self.entry_price = cur_price
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
                
            elif self.position == -1:
                # 空売り決済 (買い戻し)
                self._close_position(cur_price)
                reward += self._calc_reward()

        # Action 2: BEAR (弱気)
        elif action == 2:
            if self.position == 0:
                # 新規空売り
                self.position = -1
                self.entry_price = cur_price
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
                
            elif self.position == 1:
                # 買い決済 (売り)
                self._close_position(cur_price)
                reward += self._calc_reward()

        # Action 0: HOLD (待機)
        elif action == 0:
            if self.position == 0:
                reward += 0.005  # ノーポジ待機ボーナス
            # ポジション保有中のHOLDは報酬なし

        # ステップ進行
        self.current_step += 1
        if self.current_step >= self.MAX_STEPS:
            terminated = True
            if self.position != 0:
                self._close_position(cur_price)
                reward -= 1.0

        return self._next_observation(), float(reward), terminated, False, self._get_info()

    def _close_position(self, cur_price):
        """決済処理共通化"""
        profit = 0
        profit_pct = 0
        
        if self.position == 1: # Long Exit
            profit = cur_price - self.entry_price
            profit_pct = (cur_price - self.entry_price) / self.entry_price * 100
        elif self.position == -1: # Short Exit
            profit = self.entry_price - cur_price
            profit_pct = (self.entry_price - cur_price) / self.entry_price * 100
            
        self.balance += profit
        self.trades.append(profit_pct)
        self.last_pnl_pct = profit_pct
        self.position = 0

    def _calc_reward(self):
        """報酬計算"""
        pnl = self.last_pnl_pct
        if pnl > 0:
            self.episode_wins += 1
            return self.WIN_REWARD + pnl * 0.5
        else:
            self.episode_losses += 1
            return -self.LOSE_PENALTY + pnl * 0.2

    def _get_info(self):
        total = self.episode_wins + self.episode_losses
        win_rate = (self.episode_wins / total * 100) if total > 0 else 0
        return {
            "balance": self.balance,
            "win_rate": win_rate,
            "wins": self.episode_wins,
            "losses": self.episode_losses
        }