# -*- coding: utf-8 -*-
"""
step4_process_daily_log.py v19.5 (Append Mode)
==============================================
【v19.5 最終修正点】
1. 既存の `data/merged_xxxx.csv` がある場合、上書きせずに
   「新しいデータを末尾に追加（Append）」するように変更。
   → これにより、毎日データを蓄積してAIを賢く育てられます。
2. `--all` を使わなければ、リストにある銘柄（自分のPF）だけを処理します。
"""

import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta
import re
import yaml
import warnings
import argparse
import sys
import random

warnings.simplefilter('ignore')

# ==========================================
# 設定
# ==========================================
DEFAULT_INPUT_DIR = "data_archive_daily"
DATA_OUT_DIR = "data"
LOG_DIR = "logs"
CONFIG_FILE = "config.yaml"
TARGET_LIST_FILE = "target_list.csv"

# ランダム抽出の上限（--all使用時のみ）
MAX_PROCESS_COUNT = 300 

MIN_ROWS = 30 

# ==========================================
# 0. 引数解析 & 処理対象リスト作成
# ==========================================
def get_args():
    parser = argparse.ArgumentParser(description='Process daily logs')
    parser.add_argument('--date', type=str, help='Target date (YYYYMMDD)', default=None)
    parser.add_argument('--all', action='store_true', help=f'Process random {MAX_PROCESS_COUNT} files from archive')
    return parser.parse_args()

def create_target_list(target_date=None, process_all=False):
    if target_date:
        candidates = [
            os.path.join("data_raw", target_date),
            os.path.join("data_raw", target_date.replace("-", ""))
        ]
        input_dir = None
        for c in candidates:
            if os.path.exists(c):
                input_dir = c
                break
        
        if not input_dir:
            print(f"❌ 指定された日付のデータが見つかりません: data_raw/{target_date}")
            return set(), {}, {}
            
        print(f"📂 日付モード: {input_dir} のデータを処理します")
        all_files = glob.glob(os.path.join(input_dir, "*.csv"))
        
    else:
        input_dir = DEFAULT_INPUT_DIR
        if not os.path.exists(input_dir):
            print(f"❌ {input_dir} が空です！")
            return set(), {}, {}
        print(f"📂 アーカイブモード: {input_dir} のデータを処理します")
        all_files = glob.glob(os.path.join(input_dir, "daily_*.csv"))

    if not all_files:
        print(f"⚠️ ファイルが見つかりません: {input_dir}")
        return set(), {}, {}

    file_map = {} 
    for f in all_files:
        filename = os.path.basename(f)
        code = filename.replace("daily_", "").replace(".csv", "")
        file_map[code] = f

    if process_all:
        all_codes = list(file_map.keys())
        select_count = min(len(all_codes), MAX_PROCESS_COUNT)
        selected_codes = random.sample(all_codes, select_count)
        print(f"🎲 ランダム抽出: 全 {len(all_codes)} 銘柄から {len(selected_codes)} 銘柄を選出しました")
        final_target = set(selected_codes)
        battle_results = get_battle_results()
        return final_target, battle_results, file_map

    target_set = set()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                symbols = cfg.get("symbols", [])
                for s in symbols:
                    target_set.add(str(s.get("code")))
        except: pass

    if os.path.exists(TARGET_LIST_FILE):
        try:
            df_t = pd.read_csv(TARGET_LIST_FILE)
            for c in df_t['code'].astype(str).tolist():
                if len(c) == 5 and c.endswith('0'): c = c[:-1]
                target_set.add(c)
        except: pass

    battle_results = get_battle_results()
    final_target = {c for c in target_set if c in file_map}
    
    if target_date:
        final_target = set(file_map.keys())

    print(f"🎯 処理対象の内訳:")
    print(f"   📋 リスト要求: {len(target_set)} 銘柄")
    print(f"   📂 データあり: {len(final_target)} 銘柄")
    
    return final_target, battle_results, file_map

def get_battle_results():
    log_files = glob.glob(os.path.join(LOG_DIR, "*.log"))
    log_files += glob.glob(os.path.join("runtime", "logs", "*.log"))
    results = {} 
    re_entry = re.compile(r"LIVE\s+(?:BUY|SHORT)\s+(\d{4}[A-Z]?)")
    re_exit = re.compile(r"LIVE EXIT_EXEC (\d{4}[A-Z]?) pnl=([+\-]?\d+)")

    for filepath in log_files:
        try:
            open_positions = set()
            processed_orphans = set()
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "LIVE" not in line: continue
                    entry_match = re_entry.search(line)
                    if entry_match:
                        code = entry_match.group(1)
                        open_positions.add(code)
                        if code in processed_orphans: processed_orphans.discard(code)
                        continue
                    exit_match = re_exit.search(line)
                    if exit_match:
                        code = exit_match.group(1)
                        pnl = int(exit_match.group(2))
                        is_valid = False
                        if code in open_positions:
                            is_valid = True
                            open_positions.discard(code) 
                        else:
                            if code in processed_orphans: continue
                            else:
                                is_valid = True
                                processed_orphans.add(code)
                        if is_valid:
                            if code not in results: results[code] = 0
                            results[code] += pnl
        except: pass
    return results

def calc_rci_series(series: pd.Series, period=9) -> pd.Series:
    rci_values = []
    if len(series) < period:
        return pd.Series([np.nan] * len(series), index=series.index)
    time_rank = np.arange(1, period + 1)
    values = series.values
    n = len(values)
    for i in range(n):
        if i < period - 1:
            rci_values.append(np.nan)
            continue
        s = values[i - period + 1 : i + 1]
        price_rank = np.argsort(np.argsort(s)) + 1
        d_sq = np.sum((time_rank - price_rank) ** 2)
        val = (1 - (6 * d_sq) / (period**3 - period)) * 100
        rci_values.append(val)
    return pd.Series(rci_values, index=series.index)

def process_file(filepath, battle_score=0):
    try:
        df = pd.read_csv(filepath)
        df.columns = [c.lower() for c in df.columns]
        rename_map = {
            "date": "datetime", "timestamp": "datetime", 
            "adjc": "close", "adjo": "open", "adjh": "high", "adjl": "low", "adjvo": "volume",
            "current": "close", "price": "close", "vol": "volume"
        }
        df.rename(columns=rename_map, inplace=True)
        if 'close' not in df.columns: return None, "Close列なし"
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'], format='mixed', errors='coerce')
            df = df.dropna(subset=['datetime'])
            df.set_index('datetime', inplace=True)
        df = df[~df.index.duplicated(keep='last')]
        df.sort_index(inplace=True)
        if len(df) < MIN_ROWS: return None, f"行数不足({len(df)}<{MIN_ROWS})"

        df["close"] = pd.to_numeric(df["close"], errors='coerce').ffill()
        for col in ["open", "high", "low"]:
            if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(df["close"])
            else: df[col] = df["close"]
        if "volume" in df.columns: df["volume"] = pd.to_numeric(df["volume"], errors='coerce').fillna(0)
        else: df["volume"] = 0

        # VWAP
        df['pv'] = df['close'] * df['volume']
        df['cum_pv'] = df['pv'].cumsum()
        df['cum_vol'] = df['volume'].cumsum()
        df['vwap'] = df['cum_pv'] / (df['cum_vol'] + 1e-9)
        df['vwap'] = df['vwap'].fillna(df['close'])

        # Technicals
        try:
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
            if macd is not None: df = pd.concat([df, macd], axis=1)
        except: pass
        try:
            bb = ta.bbands(df['close'], length=20, std=2)
            if bb is not None: df = pd.concat([df, bb], axis=1)
        except: pass
        df['rci_9'] = calc_rci_series(df['close'], period=9)
        df['rci_26'] = calc_rci_series(df['close'], period=26)
        try: df['RSI_14'] = ta.rsi(df['close'], length=14)
        except: pass
        df['SMA_25'] = df['close'].rolling(25).mean()
        df['dist_sma25'] = (df['close'] - df['SMA_25']) / (df['SMA_25'] + 1e-9) * 100
        ma15 = df['close'].rolling(15).mean()
        df['dist_ma15'] = (df['close'] - ma15) / (ma15 + 1e-9)
        df['volatility'] = (df['high'] - df['low']) / (df['open'] + 1e-9)
        vol_ma = df['volume'].rolling(20).mean()
        df['vol_ratio'] = df['volume'] / (vol_ma + 1e-9)
        
        score_val = battle_score / 1000.0
        df['battle_score'] = score_val
        df = df.iloc[30:].dropna(subset=['close'])
        if len(df) == 0: return None, "テクニカル計算で全行消失"
        return df, "OK"
    except Exception as e:
        return None, str(e)

def main():
    args = get_args()
    print("=" * 60)
    print("🚀 データ加工 v19.5 (Append Mode)")
    print("=" * 60)
    os.makedirs(DATA_OUT_DIR, exist_ok=True)
    
    target_codes, battle_data, file_map = create_target_list(target_date=args.date, process_all=args.all)
    
    if not target_codes:
        print("❌ 処理できる銘柄がありません")
        return

    print(f"\n⚙️ 処理開始...")
    count = 0
    for code in target_codes:
        filepath = file_map.get(code)
        if not filepath: continue
        score = battle_data.get(code, 0)
        df, status = process_file(filepath, battle_score=score)
        
        if df is not None and len(df) > 0:
            save_path = os.path.join(DATA_OUT_DIR, f"merged_{code}.csv")
            df['code'] = code
            
            # ★変更: 既存ファイルがあれば読み込んで結合 (Append)
            if os.path.exists(save_path) and not args.all: # --allの時はリセット推奨なので上書き
                try:
                    df_old = pd.read_csv(save_path)
                    if 'datetime' in df_old.columns:
                        df_old['datetime'] = pd.to_datetime(df_old['datetime'])
                        df_old.set_index('datetime', inplace=True)
                    
                    # 結合して重複削除
                    df_new = pd.concat([df_old, df], axis=0)
                    df_new = df_new[~df_new.index.duplicated(keep='last')]
                    df_new.sort_index(inplace=True)
                    df = df_new
                    print(f"   🔄 {code}: データを追記しました (Total: {len(df)}行)")
                except:
                    print(f"   ⚠️ {code}: 追記失敗、上書きします")

            df.to_csv(save_path)
            count += 1
            if count % 50 == 0:
                print(f"✨ {count} 件処理完了...")
        else:
            pass 
            
    print("-" * 60)
    print(f"🎉 完了！ 作成ファイル数: {count} 件")
    print("=" * 60)

if __name__ == "__main__":
    main()