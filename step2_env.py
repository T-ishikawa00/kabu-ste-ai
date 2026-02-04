# -*- coding: utf-8 -*-
"""
step2_env_universal.py v22.0 (板情報対応)
==========================================
【v22.0 変更点】
1. 観測空間を21→25次元に拡張
2. 板情報特徴量を追加:
   - obi (買い板比率)
   - obi_change (5バー前との変化)
   - sell_pressure (売り圧力)
   - buy_pressure (買い圧力)
   - ndbi (深層板バランス)
3. テクニカル指標を減らし、板情報を重視
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
        # データ前処理
        # ==========================================
        df = df.copy() 
        
        # カラム正規化
        df.columns = [str(c).lower().strip() for c in df.columns]
        
        # 必須カラムの検出
        if 'close' not in df.columns:
            for alias in ['price', 'current', 'currentprice']:
                if alias in df.columns:
                    df.rename(columns={alias: 'close'}, inplace=True)
                    break
        
        if 'close' not in df.columns:
            raise ValueError(f"Fatal: 'close' column missing in {stock_code}")
        
        # 数値変換
        df['close'] = pd.to_numeric(df['close'], errors='coerce').ffill()
        df = df[df['close'] > 0].copy()

        # OHLC補完
        for col in ['high', 'low', 'open']:
            if col not in df.columns:
                df[col] = df['close']
        
        # VWAP
        if 'vwap' not in df.columns or df['vwap'].isna().mean() > 0.9:
            df['vwap'] = df['close'].expanding().mean()
        else:
            df['vwap'] = pd.to_numeric(df['vwap'], errors='coerce').fillna(df['close'])

        # Volume
        if 'volume' not in df.columns:
            df['volume'] = 0
        else:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)

        # ==========================================
        # ★ 板情報の処理
        # ==========================================
        board_cols = ['bid_qty', 'ask_qty', 'over_sell_qty', 'under_buy_qty', 
                      'obi', 'obi_change', 'sell_pressure', 'buy_pressure', 'ndbi']
        for col in board_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        # OBIがない場合は計算
        if 'obi' not in df.columns:
            if 'bid_qty' in df.columns and 'ask_qty' in df.columns:
                total = df['bid_qty'] + df['ask_qty']
                df['obi'] = np.where(total > 0, df['bid_qty'] / total, 0.5)
            else:
                df['obi'] = 0.5
        
        # OBI変化率
        if 'obi_change' not in df.columns:
            df['obi_change'] = df['obi'].diff(5).fillna(0)
        
        # 売り圧力比率
        if 'sell_pressure' not in df.columns:
            if 'over_sell_qty' in df.columns and 'ask_qty' in df.columns:
                df['sell_pressure'] = np.where(
                    df['ask_qty'] > 0,
                    df['over_sell_qty'] / (df['ask_qty'] + 1),
                    0
                )
            else:
                df['sell_pressure'] = 0
        
        # 買い圧力比率
        if 'buy_pressure' not in df.columns:
            if 'under_buy_qty' in df.columns and 'bid_qty' in df.columns:
                df['buy_pressure'] = np.where(
                    df['bid_qty'] > 0,
                    df['under_buy_qty'] / (df['bid_qty'] + 1),
                    0
                )
            else:
                df['buy_pressure'] = 0
        
        # NDBI
        if 'ndbi' not in df.columns:
            if 'over_sell_qty' in df.columns and 'under_buy_qty' in df.columns:
                total = df['over_sell_qty'] + df['under_buy_qty']
                df['ndbi'] = np.where(
                    total > 0,
                    (df['under_buy_qty'] - df['over_sell_qty']) / total,
                    0
                )
            else:
                df['ndbi'] = 0

        # ==========================================
        # テクニカル指標（最小限）
        # ==========================================
        df['ma15'] = df['close'].rolling(15, min_periods=1).mean()
        df['ma75'] = df['close'].rolling(75, min_periods=1).mean()
        
        df['dist_ma15'] = (df['close'] - df['ma15']) / (df['ma15'] + 1e-9) * 100
        df['dist_ma75'] = (df['close'] - df['ma75']) / (df['ma75'] + 1e-9) * 100
        
        try:
            rsi = df.ta.rsi(close='close', length=14)
            df['rsi_14'] = rsi.fillna(50) if rsi is not None else 50.0
        except:
            df['rsi_14'] = 50.0
        
        df['rci_9'] = self._calc_rci(df['close'], 9)
        
        vol_ma = df['volume'].rolling(20, min_periods=1).mean()
        df['vol_ratio'] = (df['volume'] / (vol_ma + 1e-9)).clip(0, 10).fillna(1.0)
        
        df['volatility'] = ((df['high'] - df['low']) / (df['open'] + 1e-9) * 100).fillna(0)
        df['mtf_trend'] = np.where(df['close'] > df['ma15'], 1.0, -1.0)
        
        df = df.ffill().bfill().fillna(0)
        if len(df) > 100:
            df = df.iloc[75:].reset_index(drop=True)
        if len(df) < 50:
            raise ValueError(f"Not enough data: {len(df)} rows")
        
        self.df = df
        self.stock_code = str(stock_code)
        self.MAX_STEPS = len(self.df) - 1

        self.TIME_LIMITS = {
            "7003": 20, "6525": 20, "6269": 20, "9984": 20, "6857": 20, "6526": 20,
            "7011": 30, "6954": 30, "5016": 30, "285A": 30, "3110": 30, "8031": 20,
            "8306": 60, "9107": 60, "5595": 20, "5838": 30
        }
        
        if self.stock_code in self.TIME_LIMITS:
            self.MAX_HOLD = self.TIME_LIMITS[self.stock_code]
        else:
            self.MAX_HOLD = max_hold

        # ==========================================
        # トレードパラメータ
        # ==========================================
        self.SLIPPAGE = 0.0005
        self.SPREAD_COST = 0.0003
        self.ENTRY_COST = 0.001
        self.STAY_REWARD = 0
        self.COUNTER_TREND_PENALTY = 0.1
        self.STOP_LOSS_PCT = -2.0
        self.PROFIT_FACTOR = 1.0
        self.LOSS_FACTOR = 1.5

        # ★ v22.0 観測空間を25次元に拡張
        self.action_space = spaces.Discrete(4)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(25,), dtype=np.float32
        )
        
        self.trades = []
        self.action_counts = [0, 0, 0, 0]

    def _calc_rci(self, series, period):
        result = pd.Series(index=series.index, dtype=float)
        result[:] = 0.0
        for i in range(period - 1, len(series)):
            window = series.iloc[i - period + 1:i + 1].values
            if len(window) < period:
                continue
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
        self.hold_duration = 0
        self.max_profit_pct = 0.0
        
        self.trades = []
        self.episode_wins = 0
        self.episode_losses = 0
        self.action_counts = [0, 0, 0, 0]
        
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
            val = row.get(key, default)
            if pd.isna(val):
                return default
            return float(val)

        vwap_val = get_val('vwap', cur_price)
        ma75_val = get_val('ma75', cur_price)
        vwap_dist = (cur_price - vwap_val) / (vwap_val + 1e-9) * 100.0
        ma75_dist = (cur_price - ma75_val) / (ma75_val + 1e-9) * 100.0
        
        trend_score = 0.0
        if cur_price > ma75_val and cur_price > vwap_val:
            trend_score = 1.0
        elif cur_price < ma75_val and cur_price < vwap_val:
            trend_score = -1.0

        # ★ 板情報
        obi = get_val('obi', 0.5)
        obi_change = get_val('obi_change', 0.0)
        sell_pressure = get_val('sell_pressure', 0.0)
        buy_pressure = get_val('buy_pressure', 0.0)
        ndbi = get_val('ndbi', 0.0)
        
        sell_pressure = np.clip(sell_pressure / 100, 0, 10)
        buy_pressure = np.clip(buy_pressure / 100, 0, 10)

        # ★ v22.0 観測ベクトル (25次元)
        obs = np.array([
            # 価格関連 (0-4)
            np.clip(pct_change, -10, 10),
            np.clip(vwap_dist, -5, 5),
            np.clip(ma75_dist, -5, 5),
            np.clip(get_val('dist_ma15', 0), -10, 10),
            trend_score,
            
            # 板情報 (5-9) ★新規
            np.clip(obi, 0, 1),
            np.clip(obi_change, -0.5, 0.5),
            np.clip(sell_pressure, 0, 10),
            np.clip(buy_pressure, 0, 10),
            np.clip(ndbi, -1, 1),
            
            # テクニカル (10-14)
            np.clip(get_val('rsi_14', 50) / 100, 0, 1),
            np.clip(get_val('rci_9', 0) / 100, -1, 1),
            np.clip(get_val('vol_ratio', 1), 0, 5),
            np.clip(get_val('volatility', 0), 0, 10),
            get_val('mtf_trend', 0),
            
            # ポジション (15-19)
            float(self.position),
            np.clip(unrealized_pnl, -5, 5),
            min(1.0, self.hold_duration / max(1, self.MAX_HOLD)),
            self.max_profit_pct,
            0.0,
            
            # 追加特徴 (20-24)
            np.clip(obi - 0.5, -0.5, 0.5) * 2,
            np.clip(buy_pressure - sell_pressure, -10, 10),
            1.0 if obi > 0.55 and ndbi > 0 else (-1.0 if obi < 0.45 and ndbi < 0 else 0.0),
            1.0 if obi_change > 0.05 else (-1.0 if obi_change < -0.05 else 0.0),
            0.0,
        ], dtype=np.float32)
        
        return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)

    def step(self, action):
        row = self.df.iloc[self.current_step]
        cur_price = float(row['close'])
        total_cost = self.SLIPPAGE + self.SPREAD_COST
        
        self.action_counts[action] += 1
        
        ma75_val = float(row.get('ma75', cur_price))
        vwap_val = float(row.get('vwap', cur_price))
        mtf_trend = float(row.get('mtf_trend', 0.0))
        
        is_uptrend = (mtf_trend > 0) and (cur_price > ma75_val)
        is_downtrend = (mtf_trend < 0) and (cur_price < ma75_val)

        reward = 0.0
        terminated = False
        
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
            
            force_exit = False
            exit_reason = ""
            
            if pnl_pct <= self.STOP_LOSS_PCT:
                force_exit = True
                exit_reason = "SL"
            elif self.hold_duration >= self.MAX_HOLD:
                force_exit = True
                exit_reason = "TIME"
            
            if force_exit:
                self._close_position(pnl_pct)
                if exit_reason == "SL":
                    reward += pnl_pct * self.LOSS_FACTOR
                    reward -= 0.1
                elif exit_reason == "TIME":
                    reward += pnl_pct * 0.5
                    reward -= 0.05
            else:
                if pnl_pct > 0:
                    reward += 0.001
                elif pnl_pct < -0.2:
                    reward -= 0.001
        
        if action == 0:
            if self.position == 0:
                reward += self.STAY_REWARD
        
        elif action == 1:
            if self.position == 0:
                self.position = 1
                self.entry_price = cur_price * (1 + total_cost)
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
                if is_downtrend:
                    reward -= self.COUNTER_TREND_PENALTY
            elif self.position == -1:
                exec_price = cur_price * (1 + total_cost)
                pnl = self._calc_realized_pnl(exec_price)
                self._close_position(pnl)
                if pnl > 0:
                    reward += pnl * self.PROFIT_FACTOR
                else:
                    reward += pnl * self.LOSS_FACTOR
                reward -= 0.02
        
        elif action == 2:
            if self.position == 0:
                self.position = -1
                self.entry_price = cur_price * (1 - total_cost)
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
                if is_uptrend:
                    reward -= self.COUNTER_TREND_PENALTY
            elif self.position == 1:
                exec_price = cur_price * (1 - total_cost)
                pnl = self._calc_realized_pnl(exec_price)
                self._close_position(pnl)
                if pnl > 0:
                    reward += pnl * self.PROFIT_FACTOR
                else:
                    reward += pnl * self.LOSS_FACTOR
                reward -= 0.02
        
        elif action == 3:
            if self.position != 0:
                if self.position == 1:
                    exec_price = cur_price * (1 - total_cost)
                else:
                    exec_price = cur_price * (1 + total_cost)
                
                pnl = self._calc_realized_pnl(exec_price)
                self._close_position(pnl)
                
                if pnl > 0:
                    reward += pnl * self.PROFIT_FACTOR
                    if self.max_profit_pct > 0.3 and pnl > self.max_profit_pct * 0.5:
                        reward += 0.05
                else:
                    reward += pnl * self.LOSS_FACTOR
                    if self.max_profit_pct > 0.3:
                        reward -= 0.1
            else:
                reward -= 0.1

        self.current_step += 1
        
        if self.current_step >= self.MAX_STEPS:
            terminated = True
            if self.position != 0:
                if self.position == 1:
                    exec_price = cur_price * (1 - total_cost)
                else:
                    exec_price = cur_price * (1 + total_cost)
                pnl = self._calc_realized_pnl(exec_price)
                self._close_position(pnl)
                reward += pnl * 0.5
                reward -= 0.05
        
        return self._next_observation(), float(reward), terminated, False, self._get_info()

    def _calc_realized_pnl(self, exec_price):
        if self.entry_price <= 1e-9:
            return 0.0
        if self.position == 1:
            return (exec_price - self.entry_price) / self.entry_price * 100
        elif self.position == -1:
            return (self.entry_price - exec_price) / self.entry_price * 100
        return 0.0

    def _close_position(self, pnl):
        self.trades.append(pnl)
        if pnl > 0:
            self.episode_wins += 1
        elif pnl < 0:
            self.episode_losses += 1
        
        self.position = 0
        self.entry_price = 0.0
        self.hold_duration = 0
        self.max_profit_pct = 0.0

    def _get_info(self):
        return {
            "wins": self.episode_wins,
            "losses": self.episode_losses,
            "trades": len(self.trades)
        }