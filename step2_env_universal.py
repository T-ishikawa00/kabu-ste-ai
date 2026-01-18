# -*- coding: utf-8 -*-
"""
step2_env_universal.py v14.0 (Pro Trader Edition)
=================================================
汎用AI専用の環境
【教育方針の転換】
- 「塩漬け」は最悪の罪。含み損の時間は毎ステップ罰を与える。
- 「早めの損切り」は正義。微損ならペナルティなし。
- 握力は「利益が出ている時」だけ発揮させる。
"""

import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces


class TradingEnv(gym.Env):
    """
    汎用AI用・プロトレーダー養成環境
    
    Action:
        0: HOLD (待機)
        1: BULL (強気) -> 新規買い / 空売り決済
        2: BEAR (弱気) -> 新規空売り / 買い決済
    """
    
    def __init__(self, df, stock_code="UNKNOWN"):
        super(TradingEnv, self).__init__()
        self.df = df.reset_index(drop=True)
        self.MAX_STEPS = len(self.df) - 1
        self.stock_code = str(stock_code)

        # パラメータ
        self.ENTRY_COST = 0.05
        self.MAX_HOLD = 60       # 最大1時間
        
        # ★損切りの基準
        self.STOP_LOSS_THRESHOLD = -0.5  # -0.5%で「危険水域」

        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(18,), dtype=np.float32
        )

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        min_start = 60
        max_start = max(min_start + 1, len(self.df) - 500)
        self.current_step = self.np_random.integers(min_start, max_start) if max_start > min_start else min_start

        self.position = 0
        self.entry_price = 0.0
        self.balance = 0.0
        self.trades = []
        self.hold_duration = 0
        self.max_profit_pct = 0.0
        self.max_drawdown_pct = 0.0  # 最大含み損も記録
        
        self.episode_wins = 0
        self.episode_losses = 0
        
        return self._next_observation(), {}

    def _next_observation(self):
        row = self.df.iloc[self.current_step]
        prev = self.df.iloc[self.current_step - 1]
        cur_price = float(row['close'])
        
        pct_change = (cur_price - float(prev['close'])) / float(prev['close']) * 100 if prev['close'] else 0

        unrealized_pnl = 0.0
        if self.position == 1:
            unrealized_pnl = (cur_price - self.entry_price) / self.entry_price * 100
        elif self.position == -1:
            unrealized_pnl = (self.entry_price - cur_price) / self.entry_price * 100

        def get_val(key, default=0.0):
            return float(row.get(key, default))

        obs = np.array([
            pct_change,
            get_val('obi', 0.5),
            get_val('MACDh_12_26_9', 0.0),
            get_val('BBP_20_2.0', 0.5),
            get_val('rci_9', 0.0) / 100.0,
            get_val('rci_26', 0.0) / 100.0,
            0.0,
            get_val('RSI_14', 50.0) / 100.0,
            get_val('vol_ratio', 1.0),
            get_val('dist_sma25', 0.0),
            float(self.position),
            unrealized_pnl,
            get_val('dist_ma15', 0.0),
            get_val('volatility', 0.0),
            self.hold_duration / self.MAX_HOLD,
            get_val('ndbi', 0.0),
            get_val('hsr', 0.0),
            get_val('battle_score', 0.0)
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
            
            # 含み損益計算
            pnl_pct = 0.0
            if self.position == 1:
                pnl_pct = (cur_price - self.entry_price) / self.entry_price * 100
            elif self.position == -1:
                pnl_pct = (self.entry_price - cur_price) / self.entry_price * 100
            
            self.max_profit_pct = max(self.max_profit_pct, pnl_pct)
            self.max_drawdown_pct = min(self.max_drawdown_pct, pnl_pct)

            # ★重要: 塩漬けへの拷問（含み損があるなら、持っているだけで罰を与える）
            if pnl_pct < 0:
                # 含み損が大きくなるほど、時間が経つほど痛くする
                pain = abs(pnl_pct) * 0.1
                reward -= pain
            
            # 利益が出ている時は、握力を褒める（少しだけプラス）
            elif pnl_pct > 0.5:
                reward += 0.01

            # 強制決済（時間切れ）
            if self.hold_duration >= self.MAX_HOLD:
                if self.position == 1: action = 2
                elif self.position == -1: action = 1
                reward -= 0.5  # 時間切れは少しカッコ悪い

        # --- 行動判定 ---
        if action == 1:  # BULL
            if self.position == 0:
                self.position = 1
                self.entry_price = cur_price
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
            elif self.position == -1:
                self._close_position(cur_price)
                reward += self._calc_reward()

        elif action == 2:  # BEAR
            if self.position == 0:
                self.position = -1
                self.entry_price = cur_price
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
            elif self.position == 1:
                self._close_position(cur_price)
                reward += self._calc_reward()

        elif action == 0:  # HOLD
            # ポジションなしで様子見していること自体は悪くない
            if self.position == 0:
                reward += 0.001

        self.current_step += 1
        if self.current_step >= self.MAX_STEPS:
            terminated = True
            if self.position != 0:
                self._close_position(cur_price)
        
        return self._next_observation(), float(reward), terminated, False, self._get_info()

    def _close_position(self, cur_price):
        profit_pct = 0
        if self.position == 1:
            profit_pct = (cur_price - self.entry_price) / self.entry_price * 100
        elif self.position == -1:
            profit_pct = (self.entry_price - cur_price) / self.entry_price * 100
            
        self.balance += profit_pct
        self.trades.append(profit_pct)
        self.last_pnl_pct = profit_pct
        self.last_hold_duration = self.hold_duration
        self.position = 0

    def _calc_reward(self):
        """
        報酬計算 v14.0 (Pro Trader Logic)
        """
        pnl = self.last_pnl_pct
        
        # ─────────────────────────────────────────
        # ケース1: 損切り (Loss Cut)
        # ─────────────────────────────────────────
        if pnl <= 0:
            self.episode_losses += 1
            
            # A. 致命傷 (-1%超え) → 即死級ペナルティ
            if pnl < -1.0:
                return -5.0 + pnl  # -5点に加え、損失分も引く
            
            # B. 微損撤退 (-0.5%以内) → ほぼ罰なし（ナイス損切り）
            if pnl > -0.5:
                # 損失分だけのマイナス（-0.1%なら -0.1点）。追加の罰はなし。
                # これにより「傷が浅いうちに逃げる」インセンティブが働く
                return pnl 
            
            # C. 普通の負け
            return -1.0 + pnl

        # ─────────────────────────────────────────
        # ケース2: 利確 (Profit)
        # ─────────────────────────────────────────
        else:
            self.episode_wins += 1
            
            # 利益はそのまま報酬に
            # 大きく勝てば大きく褒める
            return 1.0 + pnl * 2.0

    def _get_info(self):
        total = self.episode_wins + self.episode_losses
        win_rate = (self.episode_wins / total * 100) if total > 0 else 0
        return {
            "balance": self.balance,
            "win_rate": win_rate,
            "wins": self.episode_wins,
            "losses": self.episode_losses
        }