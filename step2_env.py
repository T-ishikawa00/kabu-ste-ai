# -*- coding: utf-8 -*-
"""
step2_env_universal.py v20.6 (Production Ready)
================================================
【v20.6 修正点】
1. テクニカル指標を全て計算（MACD, RSI, RCI, BB, SMA25等）
2. スリッページを現実的に（0.02% → 0.05%）
3. 報酬バランスを調整（TP/SLの非対称性を修正）
4. 待機報酬を増加（無駄なエントリーを防止）
5. tradesリストの初期化漏れを修正
6. 勝敗カウントを正しく実装

【設計思想】
- 観測データは「実際に使える値」のみ
- コストは本番と同等かやや厳しめに
- 報酬は実際のPnLに比例させる
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

        # OHLC補完（なければcloseで代用）
        if 'high' not in df.columns:
            df['high'] = df['close']
        if 'low' not in df.columns:
            df['low'] = df['close']
        if 'open' not in df.columns:
            df['open'] = df['close']

        if 'vwap' not in df.columns or df['vwap'].isna().mean() > 0.9:
            df['vwap'] = df['close'].expanding().mean()
        else:
            df['vwap'] = pd.to_numeric(df['vwap'], errors='coerce').fillna(df['close'])

        if 'volume' not in df.columns:
            df['volume'] = 0
        else:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)

        # ==========================================
        # ★v20.6: 全テクニカル指標を計算
        # ==========================================
        
        # 移動平均線
        df['ma15'] = df['close'].rolling(15, min_periods=1).mean()
        df['ma25'] = df['close'].rolling(25, min_periods=1).mean()
        df['ma75'] = df['close'].rolling(75, min_periods=1).mean()
        df['ma75_slope'] = df['ma75'].diff().fillna(0)
        
        # 乖離率
        df['dist_ma15'] = (df['close'] - df['ma15']) / (df['ma15'] + 1e-9) * 100
        df['dist_sma25'] = (df['close'] - df['ma25']) / (df['ma25'] + 1e-9) * 100
        df['dist_ma75'] = (df['close'] - df['ma75']) / (df['ma75'] + 1e-9) * 100
        
        # MACD
        try:
            macd = df.ta.macd(close='close', fast=12, slow=26, signal=9)
            if macd is not None and 'MACDh_12_26_9' in macd.columns:
                df['macdh_12_26_9'] = macd['MACDh_12_26_9'].fillna(0)
            else:
                df['macdh_12_26_9'] = 0.0
        except:
            df['macdh_12_26_9'] = 0.0
        
        # ボリンジャーバンド
        try:
            bbands = df.ta.bbands(close='close', length=20, std=2)
            if bbands is not None:
                bbl_col = [c for c in bbands.columns if 'BBL' in c]
                bbu_col = [c for c in bbands.columns if 'BBU' in c]
                if bbl_col and bbu_col:
                    bbl = bbands[bbl_col[0]].fillna(df['close'])
                    bbu = bbands[bbu_col[0]].fillna(df['close'])
                    df['bbp_20_2.0'] = (df['close'] - bbl) / (bbu - bbl + 1e-9)
                else:
                    df['bbp_20_2.0'] = 0.5
            else:
                df['bbp_20_2.0'] = 0.5
        except:
            df['bbp_20_2.0'] = 0.5
        
        # RSI
        try:
            rsi = df.ta.rsi(close='close', length=14)
            df['rsi_14'] = rsi.fillna(50) if rsi is not None else 50.0
        except:
            df['rsi_14'] = 50.0
        
        # RCI（自作）
        df['rci_9'] = self._calc_rci(df['close'], 9)
        df['rci_26'] = self._calc_rci(df['close'], 26)
        
        # 出来高比率
        vol_ma = df['volume'].rolling(20, min_periods=1).mean()
        df['vol_ratio'] = df['volume'] / (vol_ma + 1e-9)
        df['vol_ratio'] = df['vol_ratio'].clip(0, 10).fillna(1.0)
        
        # ボラティリティ（ATR風）
        df['volatility'] = ((df['high'] - df['low']) / (df['open'] + 1e-9) * 100).fillna(0)
        
        # MTFトレンド
        df['mtf_trend'] = np.where(df['close'] > df['ma15'], 1.0, -1.0)
        
        # 欠損値処理
        df = df.ffill().bfill().fillna(0)
        
        # MA75計算に必要な行を確保しつつ、十分なデータを残す
        if len(df) > 100:
            df = df.iloc[75:].reset_index(drop=True)
        
        if len(df) < 50:
            raise ValueError(f"Not enough data after processing: {len(df)} rows for {stock_code}")
        
        self.df = df
        self.stock_code = str(stock_code)
        self.MAX_STEPS = len(self.df) - 1

        # 銘柄ごとの時間制限設定 (単位: 分)
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
        # ★v20.6: 報酬パラメータ（現実的に調整）
        # ==========================================
        self.SLIPPAGE = 0.0005          # 0.05%（現実的）
        self.SPREAD_COST = 0.0003       # 0.03%
        self.ENTRY_COST = 0.003          # エントリーコスト
        self.STAY_REWARD = 0.0005        # ★増加: 待機報酬
        self.TREND_BONUS = 0.5         # トレンド一致ボーナス
        self.TREND_PENALTY = 0.1        # トレンド逆行ペナルティ
        self.COUNTER_TREND_PENALTY = 0.5  # 逆張りペナルティ

        # === ★ここに追加: 強制決済時のペナルティ ===
        self.PENALTY_SL = 1.0           # 損切りラインに達した罰（痛くする）
        self.PENALTY_TIME = 0.5         # 時間切れの罰（ダラダラ持った罰）
        self.PENALTY_TP = 0.0           # 利確ライン到達（普通は罰なしだが、設定可能に）

        # アクション: 0=STAY, 1=BUY, 2=SELL
        self.action_space = spaces.Discrete(3)
        
        # 観測空間: 21次元
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(21,), dtype=np.float32)
        
        # ★v20.6: tradesリスト初期化
        self.trades = []
        self.action_counts = [0, 0, 0]  # STAY, BUY, SHORT

    def _calc_rci(self, series, period):
        """RCI（順位相関指数）を計算"""
        result = pd.Series(index=series.index, dtype=float)
        result[:] = 0.0
        
        for i in range(period - 1, len(series)):
            window = series.iloc[i - period + 1:i + 1].values
            if len(window) < period:
                continue
            
            # 価格の順位（降順）
            price_rank = pd.Series(window).rank(ascending=False).values
            # 時間の順位（1, 2, 3, ...）
            time_rank = np.arange(1, period + 1)
            
            # RCI計算
            d_squared = np.sum((price_rank - time_rank) ** 2)
            n = period
            rci_val = (1 - 6 * d_squared / (n * (n**2 - 1))) * 100
            result.iloc[i] = rci_val
        
        return result.fillna(0)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        
        # ランダムスタート
        min_start = 10
        max_start = max(min_start + 1, len(self.df) - 500)
        if max_start <= min_start:
            self.current_step = min_start
        else:
            self.current_step = self.np_random.integers(min_start, max_start)

        self.position = 0
        self.entry_price = 0.0
        self.balance = 0.0
        self.hold_duration = 0
        self.max_profit_pct = 0.0
        
        # ★v20.6: 確実に初期化
        self.trades = []
        self.episode_wins = 0
        self.episode_losses = 0
        self.action_counts = [0, 0, 0]  # STAY, BUY, SHORT
        
        return self._next_observation(), {}

    def _next_observation(self):
        if self.current_step >= len(self.df):
            self.current_step = len(self.df) - 1
            
        row = self.df.iloc[self.current_step]
        prev = self.df.iloc[max(0, self.current_step - 1)]
        cur_price = float(row['close'])
        
        # 価格変化率
        pct_change = 0.0
        prev_close = float(prev['close'])
        if prev_close > 0:
            pct_change = (cur_price - prev_close) / prev_close * 100

        # 含み損益計算（コスト込み）
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

        # VWAP乖離
        vwap_val = get_val('vwap', cur_price)
        vwap_dist = (cur_price - vwap_val) / (vwap_val + 1e-9) * 100.0

        # MA75乖離
        ma75_val = get_val('ma75', cur_price)
        ma75_dist = (cur_price - ma75_val) / (ma75_val + 1e-9) * 100.0
        
        # 総合トレンドスコア
        trend_score = 0.0
        if cur_price > ma75_val and cur_price > vwap_val:
            trend_score = 1.0
        elif cur_price < ma75_val and cur_price < vwap_val:
            trend_score = -1.0

        # 板情報（データがあれば使用）
        obi_val = 0.5
        if 'bid_qty' in row.index and 'ask_qty' in row.index:
            bq = float(row.get('bid_qty', 0) or 0)
            aq = float(row.get('ask_qty', 0) or 0)
            if (bq + aq) > 0:
                obi_val = bq / (bq + aq)

        # ★v20.6: 21次元観測ベクトル
        obs = np.array([
            np.clip(pct_change, -10, 10),                    # 0: 価格変化率
            obi_val,                                          # 1: 板情報バランス
            np.clip(get_val('macdh_12_26_9', 0.0), -50, 50), # 2: MACDヒストグラム
            np.clip(get_val('bbp_20_2.0', 0.5), 0, 1),       # 3: ボリンジャーバンド位置
            np.clip(get_val('rci_9', 0.0) / 100.0, -1, 1),   # 4: RCI(9)
            np.clip(get_val('rci_26', 0.0) / 100.0, -1, 1),  # 5: RCI(26)
            trend_score,                                      # 6: 総合トレンドスコア
            np.clip(get_val('rsi_14', 50.0) / 100.0, 0, 1),  # 7: RSI
            np.clip(get_val('vol_ratio', 1.0), 0, 5),        # 8: 出来高比率
            np.clip(get_val('dist_sma25', 0.0), -10, 10),    # 9: SMA25乖離
            float(self.position),                             # 10: ポジション状態
            np.clip(unrealized_pnl, -5, 5),                  # 11: 含み損益
            np.clip(get_val('dist_ma15', 0.0), -10, 10),     # 12: MA15乖離
            np.clip(get_val('volatility', 0.0), 0, 10),      # 13: ボラティリティ
            min(1.0, self.hold_duration / max(1, self.MAX_HOLD)),  # 14: 保有時間比率
            0.0,                                              # 15: 予備
            0.0,                                              # 16: 予備
            0.0,                                              # 17: 予備
            np.clip(vwap_dist, -5, 5),                       # 18: VWAP乖離
            get_val('mtf_trend', 0.0),                       # 19: MTFトレンド
            np.clip(ma75_dist, -5, 5)                        # 20: MA75乖離
        ], dtype=np.float32)
        
        return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)

    def step(self, action):
        row = self.df.iloc[self.current_step]
        cur_price = float(row['close'])
        total_cost = self.SLIPPAGE + self.SPREAD_COST
        
        # ★アクションをカウント
        if hasattr(self, 'action_counts'):
            self.action_counts[action] += 1
        
        # トレンド判定
        def safe_float(val, default=0.0):
            if pd.isna(val):
                return default
            return float(val)
        
        ma75_slope = safe_float(row.get('ma75_slope'), 0.0)
        mtf_trend = safe_float(row.get('mtf_trend'), 0.0)
        ma75_val = safe_float(row.get('ma75'), cur_price)
        vwap_val = safe_float(row.get('vwap'), cur_price)
        
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
            
            if pnl_pct <= -0.8:
                force_exit = True
                exit_reason = "SL"
            elif pnl_pct >= 1.8:
                force_exit = True
                exit_reason = "TP"
            elif self.hold_duration >= self.MAX_HOLD:
                force_exit = True
                exit_reason = "TIME"
            elif self.max_profit_pct >= 0.3 and pnl_pct < (self.max_profit_pct - 0.15):
                force_exit = True
                exit_reason = "TRAIL"  # ★修正: ここが "TIME" になっていたのを "TRAIL" に直しました

            # ★v20.6: ステップ報酬（PnLに比例）
            step_reward = pnl_pct * 0.05
            
            # トレンド・オラクル報酬
            if self.position == 1 and is_uptrend:
                step_reward += self.TREND_BONUS
            elif self.position == -1 and is_downtrend:
                step_reward += self.TREND_BONUS
            elif self.position == 1 and is_downtrend:
                step_reward -= self.TREND_PENALTY
            elif self.position == -1 and is_uptrend:
                step_reward -= self.TREND_PENALTY
                
            reward += step_reward

            if force_exit:
                if self.position == 1:
                    action = 2
                elif self.position == -1:
                    action = 1
                
                # ★v20.7改: 強制決済の種類に応じた報酬とペナルティ
                if exit_reason == "TP":
                    reward += pnl_pct * 0.5   # 利確は素晴らしいのでボーナス
                    
                elif exit_reason == "SL":
                    reward += pnl_pct * 0.5   # 損失分を反映
                    reward -= self.PENALTY_SL # ★追加: 損切りラインに触れた罰（-1.0）
                    
                elif exit_reason == "TRAIL":
                    reward += pnl_pct * 0.3   # トレーリングは利益確保なので罰なし（少し褒める）
                    
                elif exit_reason == "TIME":
                    reward += pnl_pct * 0.2
                    reward -= self.PENALTY_TIME # ★追加: 時間切れの罰（-0.5）
                    if pnl_pct < 0:
                        reward -= 0.1         # 損失で終わったらさらに罰

        # --- アクション処理 ---
        if action == 1:  # BUY
            if self.position == 0:
                self.position = 1
                self.entry_price = cur_price * (1 + total_cost)
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
                
                if is_strong_downtrend:
                    reward -= self.COUNTER_TREND_PENALTY
                elif is_downtrend:
                    reward -= self.COUNTER_TREND_PENALTY * 0.5

            elif self.position == -1:  # ドテン
                exec_price = cur_price * (1 + total_cost)
                pnl = self._calc_realized_pnl(exec_price)
                self._close_position(pnl)
                reward += pnl * 0.5

        elif action == 2:  # SELL/SHORT
            if self.position == 0:
                self.position = -1
                self.entry_price = cur_price * (1 - total_cost)
                self.hold_duration = 0
                self.max_profit_pct = 0.0
                reward -= self.ENTRY_COST
                
                if is_strong_uptrend:
                    reward -= self.COUNTER_TREND_PENALTY
                elif is_uptrend:
                    reward -= self.COUNTER_TREND_PENALTY * 0.5

            elif self.position == 1:  # ドテン
                exec_price = cur_price * (1 - total_cost)
                pnl = self._calc_realized_pnl(exec_price)
                self._close_position(pnl)
                reward += pnl * 0.5

        elif action == 0:  # STAY
            if self.position == 0:
                reward += self.STAY_REWARD

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
                reward += pnl * 0.3
        
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
        self.balance += pnl
        self.trades.append(pnl)
        
        # ★v20.6: 勝敗カウント
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
            "balance": self.balance,
            "wins": self.episode_wins,
            "losses": self.episode_losses,
            "trades": len(self.trades)
        }