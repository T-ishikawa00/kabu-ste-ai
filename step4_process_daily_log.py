# -*- coding: utf-8 -*-
"""
step4_process_daily_log.py v12.0
================================
既存の merged_*.csv を v12 形式に再加工する
"""

import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta
import warnings

warnings.simplefilter('ignore')

# 設定
DATA_RAW_DIR = "data_raw"
DATA_OUT_DIR = "data"


def calc_rci_series(series: pd.Series, period=9) -> pd.Series:
    """RCI（順位相関指数）を計算"""
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


def process_existing_merged(filepath):
    """既存の merged_*.csv を読み込んで指標を再計算"""
    try:
        df = pd.read_csv(filepath)
        
        # インデックスをリセット
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
        elif 'Unnamed: 0' in df.columns:
            df['timestamp'] = pd.to_datetime(df['Unnamed: 0'])
            df.set_index('timestamp', inplace=True)
            df.drop(columns=['Unnamed: 0'], errors='ignore', inplace=True)
        
        # 必須カラムの確認
        if 'close' not in df.columns:
            print(f"  ❌ 'close' カラムがありません")
            return None
        
        # 欠損埋め
        df["close"] = pd.to_numeric(df["close"], errors='coerce').ffill()
        
        if "open" not in df.columns:
            df["open"] = df["close"]
        if "high" not in df.columns:
            df["high"] = df["close"]
        if "low" not in df.columns:
            df["low"] = df["close"]
        if "volume" not in df.columns:
            df["volume"] = 0
            
        df["open"] = pd.to_numeric(df["open"], errors='coerce').fillna(df["close"])
        df["high"] = pd.to_numeric(df["high"], errors='coerce').fillna(df["close"])
        df["low"] = pd.to_numeric(df["low"], errors='coerce').fillna(df["close"])
        df["volume"] = pd.to_numeric(df["volume"], errors='coerce').fillna(0)
        
        # 板情報（なければデフォルト値）
        if "obi" not in df.columns:
            df["obi"] = 0.5
        if "ndbi" not in df.columns:
            df["ndbi"] = 0.0
        if "hsr" not in df.columns:
            df["hsr"] = 0.0

        # ==========================================
        # テクニカル指標計算
        # ==========================================
        
        # 1. MACD
        try:
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
            if macd is not None:
                df = pd.concat([df, macd], axis=1)
        except Exception as e:
            print(f"  ⚠️ MACD計算エラー: {e}")
        
        # 2. Bollinger Bands
        try:
            bb = ta.bbands(df['close'], length=20, std=2)
            if bb is not None:
                df = pd.concat([df, bb], axis=1)
        except Exception as e:
            print(f"  ⚠️ BB計算エラー: {e}")
        
        # 3. Parabolic SAR
        try:
            psar = ta.psar(df['high'], df['low'], df['close'], af0=0.02, af=0.2)
            if psar is not None:
                df = pd.concat([df, psar], axis=1)
        except:
            pass

        # 4. RCI (順位相関指数)
        df['rci_9'] = calc_rci_series(df['close'], period=9)
        df['rci_26'] = calc_rci_series(df['close'], period=26)
        
        # 5. RSI
        try:
            rsi = ta.rsi(df['close'], length=14)
            if rsi is not None:
                df['RSI_14'] = rsi
        except:
            df['RSI_14'] = 50.0
        
        # 6. SMA
        try:
            sma = ta.sma(df['close'], length=25)
            if sma is not None:
                df['SMA_25'] = sma
        except:
            df['SMA_25'] = df['close']
        
        # 7. 出来高比率
        vol_ma = df['volume'].rolling(20).mean()
        df['vol_ratio'] = df['volume'] / (vol_ma + 1e-9)
        df['vol_ratio'] = df['vol_ratio'].replace([np.inf, -np.inf], 1.0).fillna(1.0)
        
        # 8. MA15乖離率
        ma15 = df['close'].rolling(15).mean()
        df['dist_ma15'] = (df['close'] - ma15) / (ma15 + 1e-9)
        
        # 9. ボラティリティ
        df['volatility'] = (df['high'] - df['low']) / (df['open'] + 1e-9)
        
        # 10. SMA25乖離率
        if 'SMA_25' in df.columns:
            df['dist_sma25'] = (df['close'] - df['SMA_25']) / (df['SMA_25'] + 1e-9) * 100

        # NaN行を削除（最初の30行程度）
        df = df.iloc[30:].dropna(subset=['close'])
        
        return df
        
    except Exception as e:
        print(f"  ❌ 処理エラー: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """メイン処理"""
    print("=" * 60)
    print("🚀 データ加工プロセス v12.0")
    print("=" * 60)
    
    os.makedirs(DATA_OUT_DIR, exist_ok=True)
    
    # 既存の merged_*.csv を探す
    existing_files = glob.glob(os.path.join(DATA_OUT_DIR, "merged_*.csv"))
    
    if not existing_files:
        print("❌ data/ フォルダに merged_*.csv がありません")
        print("   先にボットを実行してデータを収集してください")
        return
    
    print(f"📋 対象ファイル数: {len(existing_files)}")
    print("-" * 60)
    
    success_count = 0
    skip_count = 0
    
    for filepath in sorted(existing_files):
        filename = os.path.basename(filepath)
        stock_code = filename.replace("merged_", "").replace(".csv", "")
        
        print(f"\n📈 処理中: {stock_code}")
        
        df = process_existing_merged(filepath)
        
        if df is not None and len(df) > 100:
            # 上書き保存
            df.to_csv(filepath)
            print(f"  ✅ 完了: {len(df)}行")
            success_count += 1
        else:
            row_count = len(df) if df is not None else 0
            print(f"  ⚠️ スキップ: データ不足 ({row_count}行)")
            skip_count += 1
    
    print("\n" + "=" * 60)
    print(f"🎉 完了!")
    print(f"   成功: {success_count} 銘柄")
    print(f"   スキップ: {skip_count} 銘柄")
    print(f"\n👉 次のステップ: py -3.12 step3_train.py")
    print("=" * 60)


if __name__ == "__main__":
    main()