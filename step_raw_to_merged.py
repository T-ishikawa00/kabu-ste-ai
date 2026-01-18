# -*- coding: utf-8 -*-
"""
step_raw_to_merged.py v1.1
==========================
実践のティックデータ（data_raw/YYYYMMDD/*.csv）を
学習用データ（data/merged_*.csv）に変換・追記する

【修正履歴】
v1.1: df.get()の問題修正、CSVパースエラー対応、日付フォーマット対応
"""

import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# 設定
RAW_DIR = "data_raw"
OUTPUT_DIR = "data"
RESAMPLE_INTERVAL = "15T"  # 15分足

# 過去何日分を処理するか
DAYS_TO_PROCESS = 30


def calc_rci_series(series: pd.Series, period=9) -> pd.Series:
    """RCI計算"""
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


def safe_get_column(df, col_name, default_value=0):
    """
    DataFrameから安全にカラムを取得する
    カラムが存在しない場合はデフォルト値のSeriesを返す
    """
    if col_name in df.columns:
        return pd.to_numeric(df[col_name], errors='coerce').fillna(default_value)
    else:
        return pd.Series([default_value] * len(df), index=df.index)


def process_raw_file(file_path):
    """
    1つのティックCSVを処理して、15分足データを作成
    """
    try:
        # CSVパースエラー対策: on_bad_lines='skip'
        df = pd.read_csv(file_path, on_bad_lines='skip')
        
        if len(df) < 10:
            return None
        
        # カラム確認
        if 'timestamp' not in df.columns or 'price' not in df.columns:
            return None
        
        # 時刻をインデックスに（複数フォーマット対応）
        df['timestamp'] = pd.to_datetime(df['timestamp'], format='ISO8601', errors='coerce')
        df = df.dropna(subset=['timestamp'])
        
        if len(df) < 10:
            return None
        
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        # 数値変換（safe_get_columnを使用）
        df['price'] = pd.to_numeric(df['price'], errors='coerce')
        df['volume'] = safe_get_column(df, 'volume', 0)
        df['bid_price'] = safe_get_column(df, 'bid_price', 0)
        df['bid_qty'] = safe_get_column(df, 'bid_qty', 0)
        df['ask_price'] = safe_get_column(df, 'ask_price', 0)
        df['ask_qty'] = safe_get_column(df, 'ask_qty', 0)
        df['over_sell_qty'] = safe_get_column(df, 'over_sell_qty', 0)
        df['under_buy_qty'] = safe_get_column(df, 'under_buy_qty', 0)
        
        # NaN除去
        df = df.dropna(subset=['price'])
        if len(df) < 10:
            return None
        
        # ==========================================
        # 15分足にリサンプリング
        # ==========================================
        ohlcv = df['price'].resample(RESAMPLE_INTERVAL).ohlc()
        ohlcv.columns = ['open', 'high', 'low', 'close']
        ohlcv['volume'] = df['volume'].resample(RESAMPLE_INTERVAL).sum()
        
        # 板情報の集計（平均値）
        ohlcv['bid_qty'] = df['bid_qty'].resample(RESAMPLE_INTERVAL).mean()
        ohlcv['ask_qty'] = df['ask_qty'].resample(RESAMPLE_INTERVAL).mean()
        ohlcv['over_sell_qty'] = df['over_sell_qty'].resample(RESAMPLE_INTERVAL).mean()
        ohlcv['under_buy_qty'] = df['under_buy_qty'].resample(RESAMPLE_INTERVAL).mean()
        
        # NaN行を削除
        ohlcv = ohlcv.dropna(subset=['close'])
        
        if len(ohlcv) < 5:
            return None
        
        # ==========================================
        # 板情報指標の計算
        # ==========================================
        # OBI (Order Book Imbalance): 買い圧力の比率
        total_qty = ohlcv['bid_qty'].fillna(0) + ohlcv['ask_qty'].fillna(0) + 1e-9
        ohlcv['obi'] = ohlcv['bid_qty'].fillna(0) / total_qty
        
        # NDBI (Net Depth Bias Indicator): 隠れた需給バランス
        hidden_total = ohlcv['over_sell_qty'].fillna(0) + ohlcv['under_buy_qty'].fillna(0) + 1e-9
        ohlcv['ndbi'] = (ohlcv['under_buy_qty'].fillna(0) - ohlcv['over_sell_qty'].fillna(0)) / hidden_total
        
        # HSR (Hidden Supply Ratio): 隠れた売り圧力比率
        ohlcv['hsr'] = ohlcv['over_sell_qty'].fillna(0) / (ohlcv['over_sell_qty'].fillna(0) + ohlcv['under_buy_qty'].fillna(0) + 1e-9)
        
        # NaN埋め
        ohlcv['obi'] = ohlcv['obi'].fillna(0.5)
        ohlcv['ndbi'] = ohlcv['ndbi'].fillna(0.0)
        ohlcv['hsr'] = ohlcv['hsr'].fillna(0.5)
        
        # ==========================================
        # テクニカル指標の計算
        # ==========================================
        
        # MACD
        try:
            macd = ta.macd(ohlcv['close'], fast=12, slow=26, signal=9)
            if macd is not None:
                ohlcv = pd.concat([ohlcv, macd], axis=1)
        except:
            pass
        
        # Bollinger Bands
        try:
            bb = ta.bbands(ohlcv['close'], length=20, std=2)
            if bb is not None:
                ohlcv = pd.concat([ohlcv, bb], axis=1)
        except:
            pass
        
        # RCI
        ohlcv['rci_9'] = calc_rci_series(ohlcv['close'], period=9)
        ohlcv['rci_26'] = calc_rci_series(ohlcv['close'], period=26)
        
        # RSI
        try:
            rsi = ta.rsi(ohlcv['close'], length=14)
            if rsi is not None:
                ohlcv['RSI_14'] = rsi
        except:
            ohlcv['RSI_14'] = 50.0
        
        # SMA
        try:
            sma = ta.sma(ohlcv['close'], length=25)
            if sma is not None:
                ohlcv['SMA_25'] = sma
        except:
            ohlcv['SMA_25'] = ohlcv['close']
        
        # 出来高比率
        vol_ma = ohlcv['volume'].rolling(20, min_periods=1).mean()
        ohlcv['vol_ratio'] = ohlcv['volume'] / (vol_ma + 1e-9)
        ohlcv['vol_ratio'] = ohlcv['vol_ratio'].replace([np.inf, -np.inf], 1.0).fillna(1.0)
        
        # MA15乖離率
        ma15 = ohlcv['close'].rolling(15, min_periods=1).mean()
        ohlcv['dist_ma15'] = (ohlcv['close'] - ma15) / (ma15 + 1e-9)
        
        # ボラティリティ
        ohlcv['volatility'] = (ohlcv['high'] - ohlcv['low']) / (ohlcv['open'] + 1e-9)
        
        # SMA25乖離率
        if 'SMA_25' in ohlcv.columns:
            ohlcv['dist_sma25'] = (ohlcv['close'] - ohlcv['SMA_25']) / (ohlcv['SMA_25'] + 1e-9) * 100
        
        # インデックスをカラムに
        ohlcv.reset_index(inplace=True)
        ohlcv.rename(columns={'index': 'timestamp'}, inplace=True)
        
        return ohlcv
        
    except Exception as e:
        print(f"    ❌ エラー: {e}")
        return None


def append_to_merged(code, new_data, output_dir):
    """
    既存のmerged_*.csvに追記（重複は除く）
    """
    output_path = os.path.join(output_dir, f"merged_{code}.csv")
    
    if os.path.exists(output_path):
        # 既存データ読み込み
        existing = pd.read_csv(output_path)
        
        # timestampで重複チェック
        if 'timestamp' in existing.columns:
            existing['timestamp'] = pd.to_datetime(existing['timestamp'])
            new_data['timestamp'] = pd.to_datetime(new_data['timestamp'])
            
            existing_ts = set(existing['timestamp'].astype(str))
            new_data = new_data[~new_data['timestamp'].astype(str).isin(existing_ts)]
        
        if len(new_data) > 0:
            # 追記
            combined = pd.concat([existing, new_data], ignore_index=True)
            combined.sort_values('timestamp', inplace=True)
            combined.to_csv(output_path, index=False)
            return len(new_data)
        return 0
    else:
        # 新規作成
        new_data.to_csv(output_path, index=False)
        return len(new_data)


def main():
    print("=" * 60)
    print("🔄 実践データ → 学習データ変換ツール v1.1")
    print("=" * 60)
    print(f"📂 入力: {RAW_DIR}/YYYYMMDD/*.csv")
    print(f"📂 出力: {OUTPUT_DIR}/merged_*.csv")
    print(f"⏱️ リサンプル: {RESAMPLE_INTERVAL}")
    print(f"📅 処理日数: 過去{DAYS_TO_PROCESS}日分")
    print("-" * 60)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 処理対象の日付フォルダを取得（存在するフォルダ全て）
    target_dates = []
    if os.path.exists(RAW_DIR):
        for folder in os.listdir(RAW_DIR):
            folder_path = os.path.join(RAW_DIR, folder)
            if os.path.isdir(folder_path) and folder.isdigit() and len(folder) == 8:
                target_dates.append(folder)
    target_dates.sort(reverse=True)  # 新しい順
    
    if not target_dates:
        print("❌ 処理対象のデータがありません")
        print(f"   {RAW_DIR}/YYYYMMDD/ フォルダを確認してください")
        return
    
    print(f"📋 対象日付: {target_dates}")
    
    total_added = 0
    processed_codes = set()
    
    for date_str in target_dates:
        date_dir = os.path.join(RAW_DIR, date_str)
        csv_files = glob.glob(os.path.join(date_dir, "*.csv"))
        
        print(f"\n📅 {date_str}: {len(csv_files)}ファイル")
        
        for csv_file in csv_files:
            code = os.path.basename(csv_file).replace(".csv", "")
            
            # 4桁/5桁の数字コードを処理
            # 285Aのような英字混じりも対応
            if len(code) == 5 and code.endswith('0'):
                code = code[:-1]
            
            # 4桁で数字のみ、または4桁で英数字混合（285Aなど）
            if len(code) != 4:
                continue
            if not (code.isdigit() or (code[:3].isdigit() and code[3].isalpha())):
                continue
            
            df = process_raw_file(csv_file)
            
            if df is not None and len(df) > 0:
                added = append_to_merged(code, df, OUTPUT_DIR)
                if added > 0:
                    print(f"   ✅ {code}: +{added}行")
                    total_added += added
                    processed_codes.add(code)
    
    print("\n" + "=" * 60)
    print(f"🎉 完了！")
    print(f"   処理銘柄数: {len(processed_codes)}")
    print(f"   追加行数: {total_added}")
    print(f"\n👉 次: py -3.12 step3_train.py で再学習")
    print("=" * 60)


if __name__ == "__main__":
    main()