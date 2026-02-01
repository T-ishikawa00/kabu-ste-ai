# -*- coding: utf-8 -*-
"""
step4_process_daily_log.py v19.14 (Fix Tuples)
==============================================
【v19.14 修正点】
1. MultiIndexのカラム名（('volume', 'last')など）を確実に平坦化する処理を追加。
2. これにより、隙間埋めロジックが確実に作動し、出来高が「0」になるのを防ぐ。
"""

import os
import glob
import pandas as pd
import numpy as np
import warnings
import argparse

warnings.simplefilter('ignore')

# ==========================================
# 設定
# ==========================================
SEARCH_DIR = "data_raw"
DATA_OUT_DIR = "data"
MIN_BARS = 5 

def find_live_data_files():
    print(f"🔍 {SEARCH_DIR} 以下からCSVファイルを捜索中...")
    all_csvs = glob.glob(os.path.join(SEARCH_DIR, "**", "*.csv"), recursive=True)
    return all_csvs

def process_file(filepath):
    try:
        # 1. 読み込み
        try:
            df = pd.read_csv(filepath, encoding='utf-8-sig', on_bad_lines='skip')
        except:
            df = pd.read_csv(filepath, encoding='cp932', on_bad_lines='skip')

        # カラム名正規化
        df.columns = [str(c).lower().strip().replace("'", "").replace('"', "") for c in df.columns]
        cols = list(df.columns)

        # カラム特定
        col_dt = None
        for c in ['timestamp', 'date', 'datetime']:
            if c in cols:
                col_dt = c
                break
        
        col_close = None
        for c in ['current_price', 'currentprice', 'trade_price', 'current', 'price', 'close', 'last_price']:
            if c in cols:
                col_close = c
                break
                
        col_vol = None
        for c in ['trading_volume', 'tradingvolume', 'volume', 'vol']:
            if c in cols:
                col_vol = c
                break
                
        col_vwap = 'vwap' if 'vwap' in cols else None
        col_open = 'open' if 'open' in cols else None
        col_high = 'high' if 'high' in cols else None
        col_low = 'low' if 'low' in cols else None

        if not col_dt or not col_close: return None, "必須カラム不足"
        
        use_cols = {col_dt: 'datetime', col_close: 'close'}
        if col_vol: use_cols[col_vol] = 'volume'
        if col_vwap: use_cols[col_vwap] = 'vwap'
        if col_open: use_cols[col_open] = 'open'
        if col_high: use_cols[col_high] = 'high'
        if col_low: use_cols[col_low] = 'low'
        
        df = df[list(use_cols.keys())].rename(columns=use_cols)

        # 2. 日時変換
        df['datetime'] = pd.to_datetime(df['datetime'], format='mixed', errors='coerce')
        df = df.dropna(subset=['datetime'])
        df.set_index('datetime', inplace=True)
        df.sort_index(inplace=True)

        # 3. 数値変換
        if 'volume' in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors='coerce').fillna(0)
        else:
            df["volume"] = 0
            
        df["close"] = pd.to_numeric(df["close"], errors='coerce').ffill()
        if 'vwap' in df.columns: df['vwap'] = pd.to_numeric(df['vwap'], errors='coerce')

        # 4. リサンプリング (1分足)
        agg_dict = {'close': 'last', 'volume': 'last'}
        if 'vwap' in df.columns: agg_dict['vwap'] = 'last'
        
        if 'open' in df.columns: 
            agg_dict.update({'open':'first', 'high':'max', 'low':'min'})
        else: 
            agg_dict.update({'close': ['first', 'max', 'min', 'last']})

        df_1min = df.resample('1min').agg(agg_dict)
        
        # ★★★ カラム名整理 (ここが修正ポイント) ★★★
        if isinstance(df_1min.columns, pd.MultiIndex):
            # OHLCの生成有無で分岐
            if 'open' not in df.columns:
                # CloseからOHLCを作った場合、最初の4列はOHLC
                ohlc_cols = ['open', 'high', 'low', 'close']
                # 残りの列（volume, vwapなど）を平坦化
                other_cols = [c[0] for c in df_1min.columns[4:]]
                df_1min.columns = ohlc_cols + other_cols
            else:
                # 元からOpenがある場合、全ての列のレベル0（名前）だけを取る
                df_1min.columns = [c[0] for c in df_1min.columns]

        # 5. 欠損補完
        df_1min['close'] = df_1min['close'].ffill()
        for c in ['open', 'high', 'low', 'vwap']:
            if c in df_1min.columns: df_1min[c] = df_1min[c].fillna(df_1min['close'])

        # 6. 出来高の差分計算 (Gap Filler)
        if 'volume' in df_1min.columns:
            # 隙間を埋める
            vol_filled = df_1min['volume'].ffill()
            vol_diff = vol_filled.diff().fillna(0)
            
            # 累積かどうか判定
            if (vol_diff >= 0).mean() > 0.8:
                df_1min['volume'] = vol_diff.clip(lower=0)
            else:
                df_1min['volume'] = df_1min['volume'].fillna(0)
        
        df = df_1min.dropna(subset=['close'])
        df = df.iloc[5:] 
        
        # SMA計算
        try:
            df['SMA_25'] = df['close'].rolling(25).mean()
            df['dist_sma25'] = (df['close'] - df['SMA_25']) / (df['SMA_25'] + 1e-9) * 100
        except: pass

        if len(df) == 0: return None, "データ消失"
        
        return df, "OK"

    except Exception as e:
        return None, f"Err: {str(e)}"

def main():
    print("=" * 60)
    print("🚀 データ救出作戦 v19.14 (Fix Tuples)")
    print("=" * 60)
    os.makedirs(DATA_OUT_DIR, exist_ok=True)
    
    files = find_live_data_files()
    if not files:
        print("❌ ファイルが見つかりません")
        return

    print(f"✨ {len(files)} ファイルを処理します...")
    success_count = 0
    
    for filepath in files:
        filename = os.path.basename(filepath)
        code = filename.replace("daily_", "").replace(".csv", "")
        if "board_" in filename:
            parts = filename.split("_")
            if len(parts) >= 2: code = parts[1]

        if not any(char.isdigit() for char in code): continue

        df, status = process_file(filepath)
        
        if df is not None and len(df) > 0:
            save_path = os.path.join(DATA_OUT_DIR, f"merged_{code}.csv")
            df['code'] = code
            
            if os.path.exists(save_path):
                try:
                    df_old = pd.read_csv(save_path)
                    df_old['datetime'] = pd.to_datetime(df_old['datetime'])
                    df_old.set_index('datetime', inplace=True)
                    df_new = pd.concat([df_old, df], axis=0)
                    df_new = df_new[~df_new.index.duplicated(keep='last')]
                    df_new.sort_index(inplace=True)
                    df = df_new
                except: pass
            
            df.to_csv(save_path)
            success_count += 1
            print(f"   ✅ {code}: 成功 ({len(df)}行)")
        else:
            print(f"   ❌ {code}: {status}")

    print("-" * 60)
    print(f"🎉 完了！ 成功: {success_count} / 全体: {len(files)}")
    print("✅ 古いファイルを消してから実行しましたか？")
    print("=" * 60)

if __name__ == "__main__":
    main()