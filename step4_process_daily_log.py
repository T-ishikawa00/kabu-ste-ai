# -*- coding: utf-8 -*-
"""
step4_process_daily_log.py (v6 師匠対応版)
data_raw/ にある生ログCSVを読み込み、テクニカル指標(RCI, Parabolic, MACD, BB, OBI)を計算して
学習用データ(merged_xxxx.csv)として保存する。
"""

import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta
import warnings

# 不要な警告を消す
warnings.simplefilter('ignore')

# 設定
DATA_RAW_DIR = "data_raw"  # 今日のログがある場所 (日付フォルダ含む)
DATA_OUT_DIR = "data"      # 加工済みデータの保存先

# ==========================================
# ★共通ロジック: RCI計算関数
# ==========================================
def calc_rci(series, period=9):
    """
    RCI (Rank Correlation Index) を計算する関数
    """
    rci = []
    for i in range(len(series)):
        if i < period - 1:
            rci.append(np.nan)
            continue
        
        # 直近 period 個のデータを取得
        s = series.iloc[i - period + 1 : i + 1]
        
        # 「日付の順位」と「価格の順位」の相関を計算
        time_rank = np.arange(1, period + 1)
        price_rank = s.rank(method='max').values
        
        # RCI計算式
        d_sq = np.sum((time_rank - price_rank) ** 2)
        val = (1 - (6 * d_sq) / (period**3 - period)) * 100
        rci.append(val)
        
    return pd.Series(rci, index=series.index)

def compute_indicators_v6(df):
    """
    テクニカル指標を計算する (Step3, Controllerと同じロジック)
    師匠たちの教えバージョン (RCI, Parabolic, MACD, BB, OBI)
    """
    # 0. カラム名の整理
    if 'price' in df.columns:
        df['close'] = df['price']
    
    # データが少なすぎる場合はスキップ
    if len(df) < 15:
        return df

    try:
        # --- 1. 板気配 (OBI) ---
        if 'bid_qty' in df.columns and 'ask_qty' in df.columns:
            # 0除算防止
            df['obi'] = (df['bid_qty'] - df['ask_qty']) / (df['bid_qty'] + df['ask_qty'] + 1e-9)
        else:
            df['obi'] = 0.0

        # --- 2. トレンド系 (MACD, Parabolic, MA) ---
        # MACD (12, 26, 9)
        df.ta.macd(close='close', fast=12, slow=26, signal=9, append=True)

        # パラボリック (SAR)
        df.ta.psar(high='high', low='low', close='close', af0=0.02, af=0.2, append=True)

        # 移動平均 (SMA25)
        df.ta.sma(close='close', length=25, append=True)
        # SMA25乖離率
        if 'SMA_25' in df.columns:
            df['dist_sma25'] = (df['close'] - df['SMA_25']) / df['SMA_25'] * 100

        # --- 3. オシレーター系 (RCI, RSI, BB) ---
        # RCI (短期9, 中期26)
        df['rci_9'] = calc_rci(df['close'], period=9)
        df['rci_26'] = calc_rci(df['close'], period=26)

        # RSI (14)
        df.ta.rsi(close='close', length=14, append=True)

        # ボリンジャーバンド (20, 2)
        df.ta.bbands(close='close', length=20, std=2, append=True)

        # --- 4. 出来高系 ---
        # 出来高倍率 (20期間平均との比)
        vol_ma = df['volume'].rolling(window=20).mean()
        df['vol_ratio'] = df['volume'] / (vol_ma + 1e-9)

        # ボラティリティ (高値-安値 / 始値)
        df["volatility"] = (df["high"] - df["low"]) / (df["open"] + 1e-9)

        # 旧MA15 (互換性用)
        df["ma15"] = df["close"].rolling(window=15).mean()
        df["dist_ma15"] = (df["close"] - df["ma15"]) / (df["ma15"] + 1e-9)

    except Exception as e:
        print(f"⚠️ 指標計算中にエラー: {e}")

    # NaNを0で埋める (学習でエラーにならないように)
    df = df.fillna(0)
    return df

def process_file(filepath):
    # ファイルパスからコードを抽出
    filename = os.path.basename(filepath)
    code = filename.replace(".csv", "")
    
    print(f"🔨 Processing {code} ({filename})...")

    try:
        df_raw = pd.read_csv(filepath)
    except Exception as e:
        print(f"❌ 読み込みエラー: {e}")
        return

    if len(df_raw) == 0:
        return

    # 日付列の処理
    if "timestamp" in df_raw.columns:
        # ISOフォーマット等をdatetime型に変換
        df_raw["datetime"] = pd.to_datetime(df_raw["timestamp"])
        df_raw = df_raw.set_index("datetime")
    else:
        print("❌ timestamp列がありません")
        return
    
    # 必要なカラムのチェックと補完
    if "current_price" in df_raw.columns: df_raw["price"] = df_raw["current_price"]
    if "trading_volume" in df_raw.columns: df_raw["volume"] = df_raw["trading_volume"]
    
    if "price" not in df_raw.columns:
        return

    # --- 1分足へのリサンプリング ---
    # OHLC
    df_ohlc = df_raw["price"].resample("1min").ohlc()
    
    # 出来高 (累積値の場合は差分を取るロジックが必要だが、ここでは単純化してmax-minなどを検討)
    # ※ データ収集機(data_collector)が「その瞬間の累積出来高」を保存している前提の場合:
    #   差分を取る: .resample().last().diff()
    #   もし「その瞬間の出来高」なら .sum()
    #   今回は安全策として「その期間の最後の累積値 - 前の期間の最後の累積値」を取るのが正解だが、
    #   簡易的に「期間中のmax - min」や「期間中の変動」として扱うケースもある。
    #   ここでは「累積値の差分」として処理する。
    df_vol_cum = df_raw["volume"].resample("1min").last()
    df_vol = df_vol_cum.diff().fillna(0) # 差分が出来高
    df_vol[df_vol < 0] = 0 # マイナスはありえないので補正
    
    # 板情報 (期間中の平均)
    if "bid_qty" in df_raw.columns:
        df_bid = df_raw["bid_qty"].resample("1min").mean()
    else:
        df_bid = None
        
    if "ask_qty" in df_raw.columns:
        df_ask = df_raw["ask_qty"].resample("1min").mean()
    else:
        df_ask = None

    # 結合
    df_1m = pd.concat([df_ohlc, df_vol], axis=1)
    df_1m.columns = ["open", "high", "low", "close", "volume"]
    
    if df_bid is not None: df_1m["bid_qty"] = df_bid
    if df_ask is not None: df_1m["ask_qty"] = df_ask

    # 欠損値処理 (取引がなかった分は直前の値を引き継ぐ)
    df_1m["close"] = df_1m["close"].ffill()
    df_1m["open"] = df_1m["open"].fillna(df_1m["close"])
    df_1m["high"] = df_1m["high"].fillna(df_1m["close"])
    df_1m["low"] = df_1m["low"].fillna(df_1m["close"])
    
    # 出来高と板のNaNは0埋め
    df_1m["volume"] = df_1m["volume"].fillna(0)
    if "bid_qty" in df_1m.columns: df_1m["bid_qty"] = df_1m["bid_qty"].fillna(0)
    if "ask_qty" in df_1m.columns: df_1m["ask_qty"] = df_1m["ask_qty"].fillna(0)
    
    # 完全に空の行（closeも取れない）は削除
    df_1m = df_1m.dropna(subset=['close'])
    
    # --- 指標計算 (v6) ---
    df_1m = compute_indicators_v6(df_1m)
    
    # --- 保存 ---
    out_path = os.path.join(DATA_OUT_DIR, f"merged_{code}.csv")
    
    # 既存データがあれば追記結合
    if os.path.exists(out_path):
        try:
            df_old = pd.read_csv(out_path, index_col=0, parse_dates=True)
            # 重複を排除して結合
            df_new = pd.concat([df_old, df_1m])
            df_new = df_new[~df_new.index.duplicated(keep='last')]
            df_new.to_csv(out_path)
        except Exception as e:
             print(f"⚠️ 結合エラー(新規作成します): {e}")
             df_1m.to_csv(out_path)
    else:
        df_1m.to_csv(out_path)
    
    print(f"  -> Saved: {out_path} ({len(df_1m)} new rows)")

def main():
    if not os.path.exists(DATA_RAW_DIR):
        print(f"エラー: {DATA_RAW_DIR} がありません。")
        return
        
    os.makedirs(DATA_OUT_DIR, exist_ok=True)
    
    # 再帰的に全CSVを探す (日付フォルダ対応)
    files = glob.glob(os.path.join(DATA_RAW_DIR, "**/*.csv"), recursive=True)
    
    if not files:
        print("処理すべきCSVファイルが見つかりません。")
        return
        
    print(f"🔍 {len(files)} 個のファイルを処理します...")
    for f in files:
        process_file(f)
    print("✅ 全ファイルの処理が完了しました！")

if __name__ == "__main__":
    main()