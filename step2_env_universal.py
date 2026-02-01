# -*- coding: utf-8 -*-
"""
step2_env_universal.py v20.7 (Profit Protection)
================================================
【v20.7 変更点】
1. 「利益逸失ペナルティ」を導入 (User's Idea)
   - 最大含み益が +0.3% を超えていたのに、最終的にマイナス決済になった場合、
     強烈なペナルティ(-1.0)を与える。
   - これにより「勝ってるトレードを負けにする」悪癖を矯正する。
"""

import gymnasium as gym
import numpy as np
import pandas as pd
import pandas_ta as ta
from gymnasium import spaces

class TradingEnv(gym.Env):
    def __init__(self, df, stock_code="UNKNOWN", max_hold=60): 
        super(TradingEnv, self).__init__()
        
        # ==========================================
        # データ前処理 & テクニカル指標計算
        # ==========================================
        df = df.copy() 
        
        # カラム正規化
        new_names = {}
        raw_cols = [str(c).lower() for c in df.columns]
        found_close = False
        found_volume = False
        found_vwap = False
        found_high = False
        found_low = False
        found_open = False
        
        for i, col in enumerate(raw_cols):
            if not found_close and any(x in col for x in ['close', 'price', 'current']):
                new_names[df.columns[i]] = 'close'
                found_close = True
            if not found_volume and any(x in col for x in ['volume', 'vol', 'trading']):
                new_names[df.columns[i]] = 'volume'
                found_volume = True
            if not found_vwap and 'vwap' in col:
                new_names[df.columns[i]] = 'vwap'
                found_vwap = True
            if not found_high and 'high' in col:
                new_names[df.columns[i]] = 'high'
                found_high = True
            if not found_low and 'low' in col:
                new_names[df.columns[i]] = 'low'
                found_low = True
            if not found_open and 'open' in col:
                new_names[df.columns[i]] = 'open'
                found_open = True

        if new_names:
            df.rename(columns=new_names, inplace=True)
            df = df.loc[:, ~df.columns.duplicated()]

        if 'close' not in df.columns:
            raise ValueError(f"Fatal: 'close' column missing in {stock_code}")
        
        # 数値変換と欠損値処理
        df['close'] = pd.to_numeric(df['close'], errors='coerce').ffill()
        df = df[df['close'] > 0].copy()

        # OHLC補完
        if 'high' not in df.columns: df['high'] = df['close']
        if 'low' not in df.columns: df['low'] = df['close']
        if 'open' not in df.columns: df['open'] = df['close']

        if 'vwap' not in df.columns or df['vwap'].isna().mean() > 0.9:
            df['vwap'] = df['close'].expanding().mean()
        else:
            df['vwap'] = pd.to_numeric(df['vwap'], errors='coerce').fillna(df['close'])

        if 'volume' not in df.columns:
            df['volume'] = 0
        else:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)

        # テクニカル指標
        df['ma15'] = df['close'].rolling(15, min_periods=1).mean()
        df['ma25'] = df['close'].rolling(25, min_periods=1).mean()
        df['ma75'] = df['close'].rolling(75, min_periods=1).mean()
        df['ma75_slope'] = df['ma75'].diff().fillna(0)
        
        df['dist_ma15'] = (df['close'] - df['ma15']) / (df['ma15'] + 1e-9) * 100
        df['dist_sma25'] = (df['close'] - df['ma25']) / (df['ma25'] + 1e-9) * 100
        df['dist_ma75'] = (df['close'] - df['ma75']) / (df['ma75'] + 1e-9) * 100
        
        try:
            macd = df.ta.macd(close='close', fast=12, slow=26, signal=9)
            if macd is not None and 'MACDh_12_26_9' in macd.columns:
                df['macdh_12_26_9'] = macd['MACDh_12_26_9'].fillna(0)
            else: df['macdh_12_26_9'] = 0.0
        except: df['macdh_12_26_9'] = 0.0
        
        try:
            bbands = df.ta.bbands(close='close', length=20, std=2)
            if bbands is not None:
                bbl = bbands[next(c for c in bbands.columns if 'BBL' in c)].fillna(df['close'])
                bbu = bbands[next(c for c in bbands.columns if 'BBU' in c)].fillna(df['close'])
                df['bbp_20_2.0'] = (df['close'] - bbl) / (bbu - bbl + 1e-9)
            else: df['bbp_20_2.0'] = 0.5
        except: df['bbp_20_2.0'] = 0.5
        
        try:
            rsi = df.ta.rsi(close='close', length=14)
            df['rsi_14'] = rsi.fillna(50) if rsi is not None else 50.0
        except: df['rsi_14'] = 50.0
        
        df['rci_9'] = self._calc_rci(df['close'], 9)
        df['rci_26'] = self._calc_rci(df['close'], 26)
        
        vol_ma = df['volume'].rolling(20, min_periods=1).mean()
        df['vol_ratio'] = (df['volume'] / (vol_ma + 1e-9)).clip(0, 10).fillna(1.0)
        
        df['volatility'] = ((df['high'] - df['low']) / (df['open'] + 1e-9) * 100).fillna(0)
        df['mtf_trend'] = np.where(df['close'] > df['ma15'], 1.0, -1.0)
        
        df = df.ffill().bfill().fillna(0)
        if len(df) > 100: df = df.iloc[75:].reset_index(drop=True)
        if len(df) < 50: raise ValueError(f"Not enough data: {len(df)} rows")
        
        self.df = df
        self.stock_code = str(stock_code)
        self.MAX_STEPS = len(self.df) - 1

        self.TIME_LIMITS = {
            "7003": 20, "6525": 20, "6269": 20, "9984": 20, "6857": 20, "6526": 20,
            "7011": 30, "6954": 30, "5016": 30, "278A": 30, "285A": 30, "3110": 30, "8031": 20,
            "8306": 60, "9107": 60, "5595": 20, "5838": 30
        }
        
        if self.stock_code in self.TIME_LIMITS:
            self.MAX_HOLD = self.TIME_LIMITS[self.stock_code]
        else:
            self.MAX_HOLD = max_hold

        # ==========================================
        # 報酬パラメータ
        # ==========================================
        self.SLIPPAGE = 0.0005       # 0.05%
        self.SPREAD_COST = 0.0003    # 0.03%
        self.ENTRY_COST = 0.003
        self.STAY_REWARD = 0.0005
        self.TREND_BONUS = 0.5
        self.TREND_PENALTY = 0.1
        self.COUNTER_TREND_PENALTY = 0.5

        # ペナルティ設定
        self.PENALTY_SL = 1.0
        self.PENALTY_TIME = 0.5
        
        # ★追加: 利益逸失ペナルティ (含み益があったのに負けた場合の罰)
        self.PENALTY_OMISSION = 1.0 
        self.OMISSION_THRESHOLD = 0.3  # 最大含み益がこれ(0.3%)を超えていたのに負けたら罰

        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(21,), dtype=np.float32)
        
        self.trades = []
        self.action_counts = [0, 0, 0]

    def _calc_rci(self, series, period):
        result = pd.Series(index=series.index, dtype=float)
        result[:] = 0.0
        for i in range(period - 1, len(series)):
            window = series.iloc[i - period + 1:i + 1].values
            if len(window) < period: continue
            price_rank = pd.Series(window).rank(ascending=False).values
            time_rank = np.arange(1, period + 1)
            d_squared = np.sum((price_rank - time_rank) ** 2)
            result.iloc[i] = (1 - 6 * d_squared / (period * (period**2 - 1))) * 100
        return result.fillna(0)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        min_start = 10
        max_start = max(min_start + 1, len(self.df) - 500)
        self.current_step = min_start if max_start <= min_start else self.np_random.integers(min_start, max_start)

        self.position = 0
        self.entry_price = 0.0
        self.balance = 0.0
        self.hold_duration = 0
        self.max_profit_pct = 0.0
        
        self.trades = []
        self.episode_wins = 0
        self.episode_losses = 0
        self.action_counts = [0, 0, 0]
        
        return self._next_observation(), {}

    def _next_observation(self):
        if self.current_step >= len(self.df):
            self.current_step = len(self.df) - 1
            
        row = self.df.iloc[self.current_step]
        prev = self.df.iloc[max(0, self.current_step - 1)]
        cur_price = float(row['close'])
        
        pct_change = 0.0
        if float(prev['close']) > 0:
            pct_change = (cur_price - float(prev['close'])) / float(prev['close']) * 100

        total_cost = self.SLIPPAGE + self.SPREAD_COST
        unrealized_pnl = 0.0
        if self.position != 0 and self.entry_price > 1e-9:
            if self.position == 1:
                exit_price = cur_price * (1 - total_cost)
                unrealized_pnl = (exit_price - self.entry_price) / self.entry_price * 100
            elif self.position == -1:
                exit_price = cur_price * (1 + total_cost)
                unrealized_pnl = (self.entry_price - exit_price) / self.entry_price * 100

        def get_val(key, default=0.0):
            return float(row.get(key, default) if not pd.isna(row.get(key)) else default)

        vwap_val = get_val('vwap', cur_price)
        ma75_val = get_val('ma75', cur_price)
        
        vwap_dist = (cur_price - vwap_val) / (vwap_val + 1e-9) * 100.0
        ma75_dist = (cur_price - ma75_val) / (ma75_val + 1e-9) * 100.0
        
        trend_score = 0.0
        if cur_price > ma75_val and cur_price > vwap_val: trend_score = 1.0
        elif cur_price < ma75_val and cur_price < vwap_val: trend_score = -1.0

        obi_val = 0.5
        if 'bid_qty' in row and 'ask_qty' in row:
            bq, aq = float(row.get('bid_qty', 0) or 0), float(row.get('ask_qty', 0) or 0)
            if (bq + aq) > 0: obi_val = bq / (bq + aq)

        obs = np.array([
            np.clip(pct_change, -10, 10),
            obi_val,
            np.clip(get_val('macdh_12_26_9', 0.0), -50, 50),
            np.clip(get_val('bbp_20_2.0', 0.5), 0, 1),
            np.clip(get_val('rci_9', 0.0) / 100.0, -1, 1),
            np.clip(get_val('rci_26', 0.0) / 100.0, -1, 1),
            trend_score,
            np.clip(get_val('rsi_14', 50.0) / 100.0, 0, 1),
            np.clip(get_val('vol_ratio', 1.0), 0, 5),
            np.clip(get_val('dist_sma25', 0.0), -10, 10),
            float(self.position),
            np.clip(unrealized_pnl, -5, 5),
            np.clip(get_val('dist_ma15', 0.0), -10, 10),
            np.clip(get_val('volatility', 0.0), 0, 10),
            min(1.0, self.hold_duration / max(1, self.MAX_HOLD)),
            0.0, 0.0, 0.0, # Reserve
            np.clip(vwap_dist, -5, 5),
            get_val('mtf_trend', 0.0),
            np.clip(ma75_dist, -5, 5)
        ], dtype=np.float32)
        
        return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)

    def step(self, action):
        row = self.df.iloc[self.current_step]
        cur_price = float(row['close'])
        total_cost = self.SLIPPAGE + self.SPREAD_COST
        
        if hasattr(self, 'action_counts'):
            self.action_counts[action] += 1
        
        ma75_slope = float(row.get('ma75_slope', 0.0))
        mtf_trend = float(row.get('mtf_trend', 0.0))
        ma75_val = float(row.get('ma75', cur_price))
        vwap_val = float(row.get('vwap', cur_price))
        
        is_strong_uptrend = (ma75_slope > 0) and (mtf_trend > 0) and (cur_price > ma75_val) and (cur_price > vwap_val)
        is_strong_downtrend = (ma75_slope < 0) and (mtf_trend < 0) and (cur_price < ma75_val) and (cur_price < vwap_val)
        is_uptrend = (ma75_slope > 0) and (mtf_trend > 0)
        is_downtrend = (ma75_slope < 0) and (mtf_trend < 0)

        reward = 0.0
        terminated = False
        
        # --- ポジション保有中の処理 ---
        if self.position != 0:
            self.hold_duration += 1
            pnl_pct = 0.0
            
            if self.entry_price > 1e-9:
                if self.position == 1:
                    eval_price = cur_price * (1 - total_cost)
                    pnl_pct = (eval_price - self.entry_price) / self.entry_price * 100
                elif self.position == -1:
                    eval_price = cur_price * (1 + total_cost)
                    pnl_pct = (self.entry_price - eval_price) / self.entry_price * 100
            
            self.max_profit_pct = max(self.max_profit_pct, pnl_pct)

            # 強制決済ルール
            force_exit = False
            exit_reason = ""
            
            if pnl_pct <= -0.6:
                force_exit = True; exit_reason = "SL"
            elif pnl_pct >= 1.5:
                force_exit = True; exit_reason = "TP"
            elif self.hold_duration >= self.MAX_HOLD:
                force_exit = True; exit_reason = "TIME"
            
            # ★ここを修正: 可変式トレーリング & 建値撤退
            elif self.max_profit_pct >= 1.0:
                # 爆益モード: 1%超えたら -0.3% で利確 (例: +2.0% -> +1.7%)
                if pnl_pct < (self.max_profit_pct - 0.3):
                    force_exit = True; exit_reason = "TRAIL"
            
            elif self.max_profit_pct >= 0.3:
                # 通常モード: 0.3%〜1.0%なら「半分」で利確 (あなたのアイデア)
                # (例: +0.6% -> +0.3%)
                if pnl_pct < (self.max_profit_pct * 0.5):
                    force_exit = True; exit_reason = "TRAIL"
                    
            # 建値撤退 (微益でも逃げる)
            elif self.max_profit_pct >= 0.25 and pnl_pct < 0.02:
                force_exit = True; exit_reason = "BREAKEVEN"

            step_reward = pnl_pct * 0.05
            
            # トレンド報酬
            if self.position == 1 and is_uptrend: step_reward += self.TREND_BONUS
            elif self.position == -1 and is_downtrend: step_reward += self.TREND_BONUS
            elif self.position == 1 and is_downtrend: step_reward -= self.TREND_PENALTY
            elif self.position == -1 and is_uptrend: step_reward -= self.TREND_PENALTY
                
            reward += step_reward

            # 強制決済実行
            if force_exit:
                action = 2 if self.position == 1 else 1 # 強制反対売買
                
                if exit_reason == "TP": reward += pnl_pct * 0.5
                elif exit_reason == "SL": 
                    reward += pnl_pct * 0.5
                    reward -= self.PENALTY_SL
                elif exit_reason == "TRAIL": reward += pnl_pct * 0.3
                elif exit_reason == "TIME": 
                    reward += pnl_pct * 0.2
                    reward -= self.PENALTY_TIME
                    if pnl_pct < 0: reward -= 0.1

        # --- アクション実行と決済時のチェック ---
        
        # 決済が発生するか確認（強制または自発的）
        is_closing = False
        if self.position != 0:
            if (self.position == 1 and action == 2) or (self.position == -1 and action == 1):
                is_closing = True
        
        # アクション実行
        if action == 1: # BUY
            if self.position == 0:
                self.position = 1
                self.entry_price = cur_price * (1 + total_cost)
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
                if is_strong_downtrend: reward -= self.COUNTER_TREND_PENALTY
                elif is_downtrend: reward -= self.COUNTER_TREND_PENALTY * 0.5

            elif self.position == -1: # ドテン (決済含む)
                exec_price = cur_price * (1 + total_cost)
                pnl = self._calc_realized_pnl(exec_price)
                self._close_position(pnl)
                reward += pnl * 0.5

        elif action == 2: # SELL
            if self.position == 0:
                self.position = -1
                self.entry_price = cur_price * (1 - total_cost)
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
                if is_strong_uptrend: reward -= self.COUNTER_TREND_PENALTY
                elif is_uptrend: reward -= self.COUNTER_TREND_PENALTY * 0.5

            elif self.position == 1: # ドテン (決済含む)
                exec_price = cur_price * (1 - total_cost)
                pnl = self._calc_realized_pnl(exec_price)
                self._close_position(pnl)
                reward += pnl * 0.5

        elif action == 0: # STAY
            if self.position == 0: reward += self.STAY_REWARD

        # ★追加: 決済時の「利益逸失ペナルティ」チェック
        # 決済が行われた直後(is_closing=True)、または強制決済後
        # (self._close_positionが呼ばれるとself.tradesに追加されるので、最新のトレードを確認)
        if is_closing and len(self.trades) > 0:
            last_pnl = self.trades[-1]
            # 前回(今回)の最大含み益が閾値を超えていたのに、結果がマイナスなら罰
            if self.max_profit_pct > self.OMISSION_THRESHOLD and last_pnl < 0:
                reward -= self.PENALTY_OMISSION
                # print(f"  😡 喝！ 利益{self.max_profit_pct:.2f}%から{last_pnl:.2f}%の負けは許さん！")

        self.current_step += 1
        if self.current_step >= self.MAX_STEPS:
            terminated = True
            if self.position != 0:
                if self.position == 1: exec_price = cur_price * (1 - total_cost)
                else: exec_price = cur_price * (1 + total_cost)
                pnl = self._calc_realized_pnl(exec_price)
                self._close_position(pnl)
                reward += pnl * 0.3
        
        return self._next_observation(), float(reward), terminated, False, self._get_info()

    def _calc_realized_pnl(self, exec_price):
        if self.entry_price <= 1e-9: return 0.0
        if self.position == 1: return (exec_price - self.entry_price) / self.entry_price * 100
        elif self.position == -1: return (self.entry_price - exec_price) / self.entry_price * 100
        return 0.0

    def _close_position(self, pnl):
        self.balance += pnl
        self.trades.append(pnl)
        if pnl > 0: self.episode_wins += 1
        elif pnl < 0: self.episode_losses += 1
        self.position = 0
        self.entry_price = 0.0
        self.hold_duration = 0
        # self.max_profit_pct は次のリセットまで保持せず、ここでリセットすべきだが
        # 判定のためにstep関数内では直前の値を参照している。
        # 次のエントリー時に0.0にリセットされるのでOK。

    def _get_info(self):
        return {
            "balance": self.balance,
            "wins": self.episode_wins,
            "losses": self.episode_losses,
            "trades": len(self.trades)
        }