# -*- coding: utf-8 -*-
"""
step2_env_universal.py v19.0 (VWAP Native Support)
=================================================
【v19.0 変更点】
1. 観測空間(Observation Space)を 18 -> 19 に拡張。
   → 追加された1次元: 「VWAP乖離率」 ((株価 - VWAP) / VWAP)
2. TIME_LIMITS 辞書を最新ポートフォリオ(SCREEN等)に更新。
"""

import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces

class TradingEnv(gym.Env):
    def __init__(self, df, stock_code="UNKNOWN", max_hold=60): # デフォルトを30->60に変更（汎用向け）
        super(TradingEnv, self).__init__()
        
        # ==========================================
        # ★追加: VWAP計算 (データにない場合)
        # ==========================================
        if 'vwap' not in df.columns:
            # 累積売買代金 / 累積出来高
            df['pv'] = df['close'] * df['volume']
            df['cum_pv'] = df['pv'].cumsum()
            df['cum_vol'] = df['volume'].cumsum()
            df['vwap'] = df['cum_pv'] / (df['cum_vol'] + 1e-9)
            # 最初の数行のNaN対策
            df['vwap'] = df['vwap'].fillna(df['close'])

        self.df = df.reset_index(drop=True)
        self.MAX_STEPS = len(self.df) - 1
        self.stock_code = str(stock_code)

        # ==========================================
        # ★固定銘柄の厳格な時間割 (分)
        # ==========================================
        self.TIME_LIMITS = {
            # --- 20分 (1200秒): 暴れ馬・高ボラティリティ ---
            "7003": 20, # 三井E&S
            "6525": 20, # KOKUSAI
            "6269": 20, # 三井海洋
            "9984": 20, # SBG
            "6857": 20, # アドバンテスト
            "6526": 20, # ソシオネクスト

            # --- 30分 (1800秒): 標準・大型株 ---
            "7011": 30, # 三菱重工
            "6954": 30, # ファナック
            "5016": 30, # 良品計画
            "278A": 30, 
            "285A": 30, 
            "3110": 30, # 日東紡
            "8031": 20, # 三井物産

            # --- 60分 (3600秒): ゆったり・低ボラ ---
            "8306": 60, # 三菱UFJ
            "9107": 60, # 川崎汽船
        }

        # ==========================================
        # 時間設定の自動判定
        # ==========================================
        if self.stock_code in self.TIME_LIMITS:
            # 知っている銘柄なら、その時間を強制適用
            self.MAX_HOLD = self.TIME_LIMITS[self.stock_code]
        else:
            # 知らない銘柄なら、引数の設定に従う（汎用設定）
            self.MAX_HOLD = max_hold

        # ==========================================
        # 激辛ルール定数
        # ==========================================
        self.HARD_SL_PCT = -0.4    # 損切りライン: -0.4%
        self.HARD_TP_PCT = 1.0     # 利確ライン: +1.0%
        
        self.SL_PENALTY = 5.0        # 損切りラインに触れた時の罰金
        self.TIME_LOSS_PENALTY = 2.0 # 時間切れで「負けていた時」の罰金

        self.ENTRY_COST = 0.05
        self.STAY_BONUS = 0.002
        self.TRAIL_START = 0.3 
        self.TRAIL_CALLBACK = 0.15

        self.action_space = spaces.Discrete(3)
        
        # ★ここ変更: 18 -> 19 (VWAP乖離率を追加)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(19,), dtype=np.float32
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
        
        self.episode_wins = 0
        self.episode_losses = 0
        self.last_pnl_pct = 0.0
        
        return self._next_observation(), {}

    def _next_observation(self):
        row = self.df.iloc[self.current_step]
        prev = self.df.iloc[self.current_step - 1]
        cur_price = float(row['close'])
        
        pct_change = 0.0
        if prev['close'] and prev['close'] != 0:
            pct_change = (cur_price - float(prev['close'])) / float(prev['close']) * 100

        unrealized_pnl = 0.0
        if self.position == 1:
            unrealized_pnl = (cur_price - self.entry_price) / self.entry_price * 100
        elif self.position == -1:
            unrealized_pnl = (self.entry_price - cur_price) / self.entry_price * 100

        def get_val(key, default=0.0):
            return float(row.get(key, default))

        # ★VWAP乖離率の計算
        vwap_val = get_val('vwap', cur_price)
        if vwap_val == 0: vwap_val = cur_price
        vwap_dist = (cur_price - vwap_val) / (vwap_val + 1e-9) * 100.0

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
            get_val('battle_score', 0.0),
            vwap_dist  # ★No.19: VWAP乖離率 (ここに追加！)
        ], dtype=np.float32)
        
        return np.nan_to_num(obs)

    def step(self, action):
        row = self.df.iloc[self.current_step]
        cur_price = float(row['close'])
        reward = 0.0
        terminated = False
        
        if self.position != 0:
            self.hold_duration += 1
            
            pnl_pct = 0.0
            if self.position == 1:
                pnl_pct = (cur_price - self.entry_price) / self.entry_price * 100
            elif self.position == -1:
                pnl_pct = (self.entry_price - cur_price) / self.entry_price * 100
            
            self.max_profit_pct = max(self.max_profit_pct, pnl_pct)

            # --- 強制決済判定 ---
            force_exit = False
            exit_reason = ""
            
            # 1. 損切りライン (-0.4%以下)
            if pnl_pct <= self.HARD_SL_PCT:
                force_exit = True
                exit_reason = "HARD_SL"
            
            # 2. 利確ライン (+1.0%以上)
            elif pnl_pct >= self.HARD_TP_PCT:
                force_exit = True
                exit_reason = "HARD_TP"
            
            # 3. トレーリングストップ
            elif self.max_profit_pct >= self.TRAIL_START:
                if pnl_pct < (self.max_profit_pct - self.TRAIL_CALLBACK):
                    force_exit = True
                    exit_reason = "TRAIL"
            
            # 4. 時間切れ (★自動判定された時間で)
            elif self.hold_duration >= self.MAX_HOLD:
                force_exit = True
                exit_reason = "TIME"

            # 含み損への継続的な罰
            if not force_exit:
                if pnl_pct < 0:
                    pain = (abs(pnl_pct) ** 2) * 5.0
                    reward -= pain
                elif pnl_pct > 0:
                    reward += (pnl_pct * 0.1)
            
            # --- 決済実行と報酬 ---
            if force_exit:
                if self.position == 1: action = 2
                elif self.position == -1: action = 1
                
                # ★理由ごとの報酬設定
                if exit_reason == "HARD_SL":
                    reward = pnl_pct - self.SL_PENALTY
                elif exit_reason == "HARD_TP":
                    reward += 0.5
                elif exit_reason == "TRAIL":
                    reward += 0.5
                elif exit_reason == "TIME":
                    if pnl_pct < 0:
                        reward = pnl_pct - self.TIME_LOSS_PENALTY
                    else:
                        pass

        if action == 1: # BUY
            if self.position == 0:
                self.position = 1
                self.entry_price = cur_price
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
            elif self.position == -1:
                self._close_position(cur_price)
                reward += self._calc_trade_reward()

        elif action == 2: # SELL
            if self.position == 0:
                self.position = -1
                self.entry_price = cur_price
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
            elif self.position == 1:
                self._close_position(cur_price)
                reward += self._calc_trade_reward()

        elif action == 0:
            if self.position == 0:
                reward += self.STAY_BONUS

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
        self.position = 0

    def _calc_trade_reward(self):
        # 自発的な決済（Action 1 or 2）で呼ばれるメソッド
        pnl = self.last_pnl_pct
        if pnl > 0:
            self.episode_wins += 1
            if pnl > 0.5: return pnl * 1.5
            return pnl
        else:
            self.episode_losses += 1
            if pnl <= self.HARD_SL_PCT: 
                return pnl - self.SL_PENALTY 
            elif pnl > -0.2: 
                return pnl * 0.5
            else: 
                return pnl * 2.0

    def _get_info(self):
        return {
            "balance": self.balance,
            "wins": self.episode_wins,
            "losses": self.episode_losses
        }