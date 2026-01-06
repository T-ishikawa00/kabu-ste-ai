# -*- coding: utf-8 -*-
"""
step4_process_daily_log.py (v7.4 PSAR Fix)
パラボリック(PSAR)の交互NaNによる全行削除バグを修正したバージョン
"""

import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta
import warnings
import logging

# 不要な警告を消す
warnings.simplefilter('ignore')

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# 設定
DATA_RAW_DIR = "data_raw"  # 生ログの場所
DATA_OUT_DIR = "data"      # 加工済みデータの保存先

# ==========================================
# ★共通ロジック: RCI計算関数
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

def process_data(code):
    """
    1銘柄分のデータを統合・加工する
    """
    files = glob.glob(f"{DATA_RAW_DIR}/*/{code}.csv")
    
    if not files:
        legacy = f"{DATA_OUT_DIR}/merged_{code}.csv"
        if os.path.exists(legacy):
            files = [legacy]
        else:
            return None

    logging.info(f"📚 {code}: {len(files)} 個のファイルを処理中...")

    df_list = []
    for f in files:
        try:
            df_tmp = pd.read_csv(f)
            
            # カラム名統一
            df_tmp.columns = [c.lower() for c in df_tmp.columns]
            rename_map = {
                "price": "close", "currentprice": "close", "current_price": "close", "lasttradedprice": "close",
                "size": "volume", "volume": "volume", "tradingvolume": "volume", "trading_volume": "volume", "currentvolume": "volume",
                "bid": "bid_price", "bidprice": "bid_price",
                "ask": "ask_price", "askprice": "ask_price",
                "bid_qty": "bid_qty", "bidqty": "bid_qty",
                "ask_qty": "ask_qty", "askqty": "ask_qty"
            }
            df_tmp.rename(columns=rename_map, inplace=True)
            
            # Timestamp処理
            time_col = None
            for cand in ["timestamp", "datetime", "time"]:
                if cand in df_tmp.columns:
                    time_col = cand
                    break
            
            if time_col:
                df_tmp["timestamp"] = pd.to_datetime(df_tmp[time_col], format='mixed')
            else:
                continue 
            
            df_tmp.set_index("timestamp", inplace=True)
            
            # 1分足にリサンプリング
            if "close" not in df_tmp.columns: continue
            
            # 数値変換
            for col in ["close", "volume", "bid_qty", "ask_qty"]:
                if col in df_tmp.columns:
                    df_tmp[col] = pd.to_numeric(df_tmp[col], errors='coerce')

            df_resampled = df_tmp["close"].resample("1T").ohlc()
            
            if "volume" in df_tmp.columns:
                df_resampled["volume"] = df_tmp["volume"].resample("1T").sum()
            else:
                df_resampled["volume"] = 0

            # 板情報
            if "bid_qty" in df_tmp.columns and "ask_qty" in df_tmp.columns:
                bid_q = df_tmp["bid_qty"].resample("1T").mean()
                ask_q = df_tmp["ask_qty"].resample("1T").mean()
                df_resampled["obi"] = bid_q / (bid_q + ask_q + 1e-9)
            else:
                df_resampled["obi"] = 0.5

            df_list.append(df_resampled)
            
        except Exception as e:
            continue
    
    if not df_list:
        return None

    # 結合 & ソート
    df = pd.concat(df_list).sort_index()
    df = df[~df.index.duplicated(keep='last')]
    
    # 欠損埋め (価格は直前値、出来高は0)
    df["close"] = df["close"].ffill()
    df["open"] = df["open"].fillna(df["close"])
    df["high"] = df["high"].fillna(df["close"])
    df["low"] = df["low"].fillna(df["close"])
    df["volume"] = df["volume"].fillna(0)
    df["obi"] = df["obi"].fillna(0.5)

    # ==========================================
    # ★ テクニカル指標計算
    # ==========================================
    try:
        # 1. MACD
        df.ta.macd(close='close', fast=12, slow=26, signal=9, append=True)
        
        # 2. BB
        df.ta.bbands(close='close', length=20, std=2, append=True)
        
        # 3. PSAR (ここが問題児)
        try:
            df.ta.psar(high='high', low='low', close='close', af0=0.02, af=0.2, append=True)
        except:
            pass

        # 4. RCI
        df['rci_9'] = calc_rci_series(df['close'], period=9)
        df['rci_26'] = calc_rci_series(df['close'], period=26)
        
        # 5. Others
        df.ta.rsi(close='close', length=14, append=True)
        df.ta.sma(close='close', length=25, append=True)
        
        vol_ma = df['volume'].rolling(20).mean()
        df['vol_ratio'] = df['volume'] / (vol_ma + 1e-9)
        
        ma15 = df['close'].rolling(15).mean()
        df['dist_ma15'] = (df['close'] - ma15) / (ma15 + 1e-9)
        
        df['volatility'] = (df['high'] - df['low']) / (df['open'] + 1e-9)
        
        if 'SMA_25' in df.columns:
            df['dist_sma25'] = (df['close'] - df['SMA_25']) / (df['SMA_25'] + 1e-9) * 100

        # ★修正: 全削除(dropna)ではなく、計算に必要な「最初の30行」だけを削除する
        # PSARのNaNは残しておかないと、TradingEnvで読み込めない
        
        # 必須指標(MACD, RCI26, SMA25)が計算できていない行だけを削除
        essential_cols = ['rci_26', 'SMA_25']
        # MACDの列名は環境によって変わる可能性があるため、存在チェック
        for c in df.columns:
            if "MACD" in c and "12_26" in c:
                essential_cols.append(c)
                break
        
        # 念のためカラムが存在するかチェックしてから削除
        target_cols = [c for c in essential_cols if c in df.columns]
        
        if target_cols:
            df.dropna(subset=target_cols, inplace=True)
        else:
            # 万が一カラムが見つからない場合は最初の30行を一律削除
            df = df.iloc[30:]
        
    except Exception as e:
        logging.error(f"指標計算エラー {code}: {e}")
        return None

    return df

def get_code_from_filename(filename):
    parts = filename.replace(".csv", "").split("_")
    if filename.startswith("board_") and len(parts) >= 3:
        if parts[1].isdigit() or (len(parts[1])==4): 
            return parts[1]
    if parts[0].isdigit() or (len(parts[0])==4 and parts[0][0].isdigit()):
        return parts[0]
    return None

def main():
    if not os.path.exists(DATA_OUT_DIR):
        os.makedirs(DATA_OUT_DIR)

    all_files = glob.glob(os.path.join(DATA_RAW_DIR, "**/*.csv"), recursive=True)
    target_codes = set()
    
    for f in all_files:
        code = get_code_from_filename(os.path.basename(f))
        if code:
            target_codes.add(code)
            
    existing_merged = glob.glob(os.path.join(DATA_OUT_DIR, "merged_*.csv"))
    for f in existing_merged:
        code = os.path.basename(f).replace("merged_", "").replace(".csv", "")
        target_codes.add(code)

    print(f"🚀 データ加工プロセス開始 (Audit v7.4 PSAR Fix)")
    print(f"📋 対象銘柄数: {len(target_codes)}")
    print("=" * 50)

    for code in sorted(target_codes):
        df = process_data(code)
        if df is not None and len(df) > 50:
            out_path = f"{DATA_OUT_DIR}/merged_{code}.csv"
            df.to_csv(out_path)
            print(f"✅ {code}: 保存完了 ({len(df)}行) -> {out_path}")
        else:
            print(f"⚠️ {code}: データ不足 (有効行数: {len(df) if df is not None else 0})")

    print("=" * 50)
    print("🎉 全工程完了。これでAI学習(Step3)に進めます。")

if __name__ == "__main__":
    main()