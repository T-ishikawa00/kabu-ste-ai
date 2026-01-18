# -*- coding: utf-8 -*-
"""
tool_generate_training_data.py
==============================
Step 1 で収集した大量の「日足データ(daily_*.csv)」から、
AI学習用の「統合データ(merged_*.csv)」を生成するツール。

・売買する必要はありません。
・J-Quantsのデータを使って、AIに「仮想的な経験」を積ませます。
・ランダムに銘柄を選んで学習データの多様性を確保します。
"""

import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta
import random

# 設定
INPUT_DIR = "data_archive_daily"  # Step 1 で集めたデータ
OUTPUT_DIR = "data"               # AIが学習に使うフォルダ
MAX_SAMPLES = 100                 # 追加する銘柄数（多すぎると学習が重くなるので調整）

# ---------------------------------------------------------
# 指標計算ロジック (Step 4 と同じもの)
# ---------------------------------------------------------
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

def process_daily_file(file_path):
    try:
        df = pd.read_csv(file_path)
        
        # カラム名の正規化
        if 'timestamp' not in df.columns and 'datetime' in df.columns:
            df.rename(columns={'datetime': 'timestamp'}, inplace=True)
            
        # 必須データの確認
        required = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required):
            return None
            
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.sort_values('timestamp', inplace=True)
        df.reset_index(drop=True, inplace=True)
        
        # データ不足を除外
        if len(df) < 300:
            return None

        # ----------------------------------------------
        # テクニカル指標の追加 (AIの目)
        # ----------------------------------------------
        
        # MACD
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        if macd is None: return None
        df = pd.concat([df, macd], axis=1)

        # Bollinger Bands
        bb = ta.bbands(df['close'], length=20, std=2)
        if bb is None: return None
        df = pd.concat([df, bb], axis=1)

        # RCI
        df['rci_9'] = calc_rci_series(df['close'], period=9)
        df['rci_26'] = calc_rci_series(df['close'], period=26)

        # RSI
        df['RSI_14'] = ta.rsi(df['close'], length=14)

        # SMA & 乖離率
        df['SMA_25'] = ta.sma(df['close'], length=25)
        df['dist_sma25'] = (df['close'] - df['SMA_25']) / df['SMA_25'] * 100

        # 移動平均乖離 (MA15)
        ma15 = df['close'].rolling(15).mean()
        df['dist_ma15'] = (df['close'] - ma15) / ma15

        # 出来高変化率
        vol_ma = df['volume'].rolling(20).mean()
        df['vol_ratio'] = df['volume'] / (vol_ma + 1e-9)
        
        # ボラティリティ
        df['volatility'] = (df['high'] - df['low']) / df['open']

        # 板情報（過去データにはないのでダミー値を入れる）
        # ※汎用モデル学習には「板情報がない状態」も混ぜることで頑健になる
        df['obi'] = 0.5
        df['ndbi'] = 0.0
        df['hsr'] = 0.0
        
        # NaN除去
        df.dropna(inplace=True)
        
        return df

    except Exception:
        return None

def main():
    print("=" * 60)
    print("🏭 AI学習データ生成ツール")
    print("   〜倉庫のデータからAIの教科書を作る〜")
    print("=" * 60)

    # 1. 候補を探す
    daily_files = glob.glob(os.path.join(INPUT_DIR, "daily_*.csv"))
    if not daily_files:
        print("❌ dailyデータがありません。先に step1 を実行してください。")
        return

    print(f"📚 倉庫内のファイル数: {len(daily_files)} 銘柄")
    
    # 2. すでにmergedがある銘柄は除外（重複防止）
    existing_merged = glob.glob(os.path.join(OUTPUT_DIR, "merged_*.csv"))
    existing_codes = [os.path.basename(f).replace("merged_", "").replace(".csv", "") for f in existing_merged]
    
    candidates = []
    for f in daily_files:
        code = os.path.basename(f).replace("daily_", "").replace(".csv", "")
        if code not in existing_codes:
            candidates.append(f)
            
    print(f"🆕 新規追加可能な候補: {len(candidates)} 銘柄")
    
    if not candidates:
        print("   ※すべて変換済みです。")
        return

    # 3. ランダムにピックアップ
    target_files = random.sample(candidates, min(len(candidates), MAX_SAMPLES))
    print(f"🎯 今回変換する銘柄数: {len(target_files)} (ランダム選出)")
    
    count = 0
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("\n🔨 変換中...")
    for file_path in target_files:
        code = os.path.basename(file_path).replace("daily_", "").replace(".csv", "")
        
        df_processed = process_daily_file(file_path)
        
        if df_processed is not None:
            save_path = os.path.join(OUTPUT_DIR, f"merged_{code}.csv")
            df_processed.to_csv(save_path, index=False)
            print(f"   ✅ 生成: {code} ({len(df_processed)}行)")
            count += 1
        else:
            # データ不足などで失敗した場合
            pass
            
    print("-" * 60)
    print(f"🎉 完了！ 新たに {count} 銘柄の学習データを追加しました。")
    print(f"👉 次は: py -3.12 train_universal.py を実行してAIを再教育してください。")

if __name__ == "__main__":
    main()