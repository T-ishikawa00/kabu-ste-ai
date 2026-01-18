# -*- coding: utf-8 -*-
"""
step4_process_daily_log.py v15.0 (Random Injection Edition)
===========================================================
【最強のデータセット作成機能】
1. あなたの「戦った銘柄」と「監視銘柄」を最優先でリストアップ
2. そこに「ランダムな野良銘柄」を100個追加
3. 全てに「最新の戦績注入ロジック」を適用して保存

これにより、AIは「あなたの戦績」を重視しつつ、
「初見の銘柄」にも対応できる汎用性を身につけます。
"""

import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta
import re
import yaml
import random
import warnings

warnings.simplefilter('ignore')

# ==========================================
# 設定
# ==========================================
DATA_INPUT_DIR = "data_archive_daily"  # Step1が保存している場所
LOG_DIR = "logs"                       # main.pyが保存している場所
DATA_OUT_DIR = "data"                  # AIが読む場所 (出力先)
CONFIG_FILE = "config.yaml"            # 監視銘柄リスト

RANDOM_SAMPLE_COUNT = 100              # ランダムに追加する「練習相手」の数
MIN_ROWS = 30

# ==========================================
# 0. 処理対象リスト作成（優先枠＋ランダム枠）
# ==========================================
def create_target_list():
    """
    処理すべき銘柄のリストを作成する
    Priority 1: 戦績がある銘柄 (Logs)
    Priority 2: 監視している銘柄 (Config)
    Priority 3: ランダムな野良銘柄 (Random)
    """
    # 1. 倉庫にある全ファイルを取得
    all_files = glob.glob(os.path.join(DATA_INPUT_DIR, "daily_*.csv"))
    if not all_files:
        print(f"❌ {DATA_INPUT_DIR} が空です！ Step1を実行してください。")
        return set(), {}, []

    # コード一覧を抽出
    all_codes = []
    file_map = {} # code -> filepath
    for f in all_files:
        code = os.path.basename(f).replace("daily_", "").replace(".csv", "")
        all_codes.append(code)
        file_map[code] = f

    # --- Priority 1 & 2: エリート枠（戦績・監視） ---
    priority_whitelist = set()
    
    # Configから
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                symbols = cfg.get("symbols", [])
                for s in symbols:
                    priority_whitelist.add(str(s.get("code")))
        except: pass

    # Logから (戦績取得)
    battle_results = get_battle_results()
    for code in battle_results.keys():
        priority_whitelist.add(code)
    
    # --- Priority 3: ランダム枠（練習相手） ---
    # エリート枠に含まれていない銘柄を候補にする
    candidates = [c for c in all_codes if c not in priority_whitelist]
    
    random_picks = []
    if len(candidates) > RANDOM_SAMPLE_COUNT:
        random_picks = random.sample(candidates, RANDOM_SAMPLE_COUNT)
    else:
        random_picks = candidates # 足りなければ全部
    
    # 最終リスト結合
    final_target = priority_whitelist.union(set(random_picks))
    
    print(f"🎯 処理対象の内訳:")
    print(f"   👑 優先枠 (戦績/監視): {len(priority_whitelist)} 銘柄")
    print(f"   🎲 野良枠 (ランダム): {len(random_picks)} 銘柄")
    print(f"   ∑  合計: {len(final_target)} 銘柄")
    
    return final_target, battle_results, file_map

# ==========================================
# 1. 戦績データの解析機能
# ==========================================
def get_battle_results():
    """ログから戦績を集計"""
    log_files = glob.glob(os.path.join(LOG_DIR, "*.log"))
    log_files += glob.glob(os.path.join("runtime", "logs", "*.log"))
    
    results = {} 
    pattern = re.compile(r"LIVE EXIT_EXEC (\d{4}) pnl=([+\-]?\d+)")
    
    for filepath in log_files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    match = pattern.search(line)
                    if match:
                        code = match.group(1)
                        pnl = int(match.group(2))
                        if code not in results: results[code] = 0
                        results[code] += pnl
        except: pass
    return results

# ==========================================
# 2. 指標計算ロジック
# ==========================================
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
        rename_map = {
            "Date": "datetime", "date": "datetime", 
            "Open": "open", "High": "high", "Low": "low", "Close": "close",
            "Volume": "volume", "AdjC": "close", "AdjO": "open", "AdjH": "high", "AdjL": "low", "AdjVo": "volume"
        }
        df.rename(columns=rename_map, inplace=True)
        if 'close' not in df.columns: return None

        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
            df.set_index('datetime', inplace=True)
        
        df = df[~df.index.duplicated(keep='last')]
        df.sort_index(inplace=True)

        df["close"] = pd.to_numeric(df["close"], errors='coerce').ffill()
        for col in ["open", "high", "low"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(df["close"])
            else:
                df[col] = df["close"]
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors='coerce').fillna(0)
        else:
            df["volume"] = 0

        # --- テクニカル指標 ---
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

        try:
            df['RSI_14'] = ta.rsi(df['close'], length=14)
        except: pass
        
        df['SMA_25'] = df['close'].rolling(25).mean()
        df['dist_sma25'] = (df['close'] - df['SMA_25']) / (df['SMA_25'] + 1e-9) * 100
        
        ma15 = df['close'].rolling(15).mean()
        df['dist_ma15'] = (df['close'] - ma15) / (ma15 + 1e-9)
        
        df['volatility'] = (df['high'] - df['low']) / (df['open'] + 1e-9)
        
        vol_ma = df['volume'].rolling(20).mean()
        df['vol_ratio'] = df['volume'] / (vol_ma + 1e-9)
        
        # --- ★戦績スコア注入 ---
        # 優先枠ならスコアあり、野良枠なら0
        score_val = battle_score / 1000.0
        df['battle_score'] = score_val

        df = df.iloc[30:].dropna(subset=['close'])
        return df

    except Exception:
        return None

def main():
    print("=" * 60)
    print("🚀 データ加工 v15.0 (Random Injection)")
    print("   優先枠 + ランダム100銘柄で最強のデータセットを作成")
    print("=" * 60)
    
    os.makedirs(DATA_OUT_DIR, exist_ok=True)
    
    # 1. 処理対象リストを作成
    target_codes, battle_data, file_map = create_target_list()
    
    if not target_codes:
        return

    print(f"\n⚙️ 処理開始...")
    print("-" * 60)
    
    count = 0
    injected_count = 0
    
    for code in target_codes:
        # ファイルパス取得
        filepath = file_map.get(code)
        if not filepath: continue

        # 戦績スコアを取得（野良枠は0になる）
        score = battle_data.get(code, 0)
        
        # 加工実行
        df = process_file(filepath, battle_score=score)
        
        if df is not None and len(df) > 0:
            save_path = os.path.join(DATA_OUT_DIR, f"merged_{code}.csv")
            df['code'] = code
            df.to_csv(save_path)
            
            if score != 0:
                print(f"🔥 {code} (戦績:¥{score:,}) -> 優先注入")
                injected_count += 1
            elif count % 20 == 0:
                print(f"🎲 {code} (野良) -> 追加")
            
            count += 1
            
    print("-" * 60)
    print(f"🎉 完了！")
    print(f"   作成ファイル数: {count} 件")
    print(f"   (内訳: 戦績入り {injected_count} 件 / 野良 {count - injected_count} 件)")
    print(f"\n👉 次のステップ: py -3.12 train_universal.py")
    print("   ※ データ量が増えたので、学習には少し時間がかかります")
    print("=" * 60)

if __name__ == "__main__":
    main()