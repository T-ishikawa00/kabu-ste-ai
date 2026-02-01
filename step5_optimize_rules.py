# -*- coding: utf-8 -*-
"""
step5_optimize_rules.py v19.7 (Real Slippage)
============================================
【戦略研究所 v19.7】
「成行注文の不利な約定(スリッページ)」をシミュレーションに完全実装。
手数料負け・スプレッド負けする甘い戦略を徹底排除します。
"""

import os
import glob
import pandas as pd
import numpy as np
import optuna
import logging
import warnings

warnings.filterwarnings('ignore') 
logging.getLogger("optuna").setLevel(logging.WARNING)

DATA_DIR = "data"
MIN_TRADES = 1 
MAX_HOLD_BARS = 30 

# ★成行スリッページ設定 (片道 0.05% = 往復 0.1% のコスト)
# 株価 2000円なら 1円、4000円なら 2円 ズレると想定
SLIPPAGE_PCT = 0.0005 

def normalize_column_names(df):
    new_names = {}
    raw_cols = [str(c).lower() for c in df.columns]
    found_close = False
    found_volume = False
    found_vwap = False
    
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

    if new_names:
        df.rename(columns=new_names, inplace=True)
        df = df.loc[:, ~df.columns.duplicated()]
    return df

def load_data():
    files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not files: return []
    data_list = []
    for f in files:
        try:
            df = pd.read_csv(f)
            df = normalize_column_names(df)
            if 'close' not in df.columns: continue
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            if 'vwap' not in df.columns: df['vwap'] = df['close']
            else: df['vwap'] = pd.to_numeric(df['vwap'], errors='coerce').fillna(df['close'])
            if 'volume' in df.columns:
                df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
                vol_ma = df['volume'].rolling(20).mean()
                df['vol_ratio'] = df['volume'] / (vol_ma + 1e-9)
            else: df['vol_ratio'] = 1.0
            df = df.dropna(subset=['close'])
            if len(df) > 50: data_list.append(df)
        except: continue
    return data_list

ALL_DFS = load_data()

def simulate_strategy(df, tp_pct, sl_pct, vwap_dist_threshold, entry_vol_boost):
    if df.empty: return 0, 0, 0
    closes = df['close'].values
    vwaps = df['vwap'].values
    vol_ratios = df['vol_ratio'].values
    
    n = len(closes)
    capital = 0.0
    trades = 0
    wins = 0
    position = 0 
    entry_price = 0.0
    hold_bars = 0
    
    for i in range(1, n):
        price = closes[i]
        vwap = vwaps[i]
        
        if position == 0:
            dist = abs(price - vwap) / (vwap + 1e-9) * 100
            if dist <= vwap_dist_threshold and vol_ratios[i] >= entry_vol_boost:
                if price > vwap: # Long
                    position = 1
                    # ★成行で高く買わされる (Ask価格)
                    entry_price = price * (1 + SLIPPAGE_PCT)
                    hold_bars = 0
                elif price < vwap: # Short
                    position = -1
                    # ★成行で安く売らされる (Bid価格)
                    entry_price = price * (1 - SLIPPAGE_PCT)
                    hold_bars = 0
        else:
            hold_bars += 1
            pnl_pct = 0.0
            is_closed = False
            
            # 含み損益の計算 (決済時に不利な価格になる想定)
            if position == 1:
                # 売る時は現在値より安くなる
                exit_eval = price * (1 - SLIPPAGE_PCT)
                pnl_pct = (exit_eval - entry_price) / entry_price * 100
            elif position == -1:
                # 買う時は現在値より高くなる
                exit_eval = price * (1 + SLIPPAGE_PCT)
                pnl_pct = (entry_price - exit_eval) / entry_price * 100
            
            # 決済判定
            if pnl_pct >= tp_pct:
                capital += pnl_pct
                trades += 1
                wins += 1
                is_closed = True
            elif pnl_pct <= -sl_pct:
                capital += pnl_pct
                trades += 1
                is_closed = True
            elif hold_bars >= MAX_HOLD_BARS:
                capital += pnl_pct
                trades += 1
                if pnl_pct > 0: wins += 1
                is_closed = True
            
            if is_closed:
                position = 0
                hold_bars = 0
    return capital, trades, wins

def objective(trial):
    tp_pct = trial.suggest_float("tp_pct", 0.5, 3.0, step=0.1) # スリッページがあるので下限を0.5に引上げ
    sl_pct = trial.suggest_float("sl_pct", 0.3, 2.0, step=0.1) # 同様に下限を0.3に
    vwap_dist = trial.suggest_float("vwap_dist", 0.1, 3.0, step=0.1)
    vol_boost = trial.suggest_float("vol_boost", 1.0, 3.0, step=0.1)
    
    total_score = 0.0
    total_trades = 0
    for df in ALL_DFS:
        pnl, count, w = simulate_strategy(df, tp_pct, sl_pct, vwap_dist, vol_boost)
        total_score += pnl
        total_trades += count
    
    if total_trades < MIN_TRADES: return -9999
    return total_score

def run_optimization():
    if not ALL_DFS:
        print("❌ データなし")
        return
    print("=" * 60)
    print("🔬 戦略研究所 v19.7 - 最適化 (Slippage: 0.05%)")
    print(f"   データ数: {len(ALL_DFS)} ファイル")
    print("=" * 60)
    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=300, show_progress_bar=True)
    
    best = study.best_params
    print(f"\n📊 [現実的な推奨設定]")
    print(f"common:")
    print(f"  tp_pct: {best['tp_pct']:.1f}")
    print(f"  sl_pct: {best['sl_pct']:.1f}")
    print(f"router:")
    print(f"  logic:")
    print(f"    vwap_mechanic:")
    print(f"      allowed_dist: {best['vwap_dist']:.2f}")
    print(f"      volume_boost: {best['vol_boost']:.1f}")
    print("-" * 60)
    print(f"💰 期待スコア (実残益): {study.best_value:.2f}")
    print("=" * 60)

if __name__ == "__main__":
    run_optimization()