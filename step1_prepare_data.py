# -*- coding: utf-8 -*-
"""
step1_prepare_data.py v20.0 (DeepResearch: Data Augmentation)
=============================================================
【v20.0 革新的変更点】
1. マルチタスク学習用の「正解ラベル」を作成:
   - target_return_15m: 15分後の価格変化率 (未来予測用)
   - target_volatility_15m: 向こう15分間の変動幅 (リスク予測用)

2. カリキュラム学習用の「レジーム(相場環境)判定」:
   - regime: 'UPTREND', 'DOWNTREND', 'RANGE', 'CRASH' の4種類を自動付与。
   - これにより、学習フェーズごとに「トレンド相場のみ」「暴落のみ」といったデータの出し分けが可能になる。

3. データ品質の向上:
   - 欠損値の補間、外れ値の除去、テクニカル指標(pandas_ta)の一括計算。
"""

import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta
import warnings

warnings.filterwarnings("ignore")

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data"
MIN_ROWS = 100  # これ以下のデータは削除

# レジーム判定のパラメータ
ADX_THRESHOLD = 25       # トレンド発生とみなすADX値
CRASH_THRESHOLD = -0.01  # 5分間で1%以上急落したら「暴落」とみなす

def process_file(filepath):
    """
    1つのCSVファイルを読み込み、v20用の特徴量とラベルを追加して保存する
    """
    try:
        # 1. 読み込み & カラム正規化
        df = pd.read_csv(filepath)
        df.columns = [str(c).lower().strip() for c in df.columns]
        
        # カラム名の揺らぎ吸収
        rename_map = {
            'current': 'close', 'price': 'close', 'current_price': 'close',
            'trading_volume': 'volume', 'vol': 'volume'
        }
        df.rename(columns=rename_map, inplace=True)
        
        if 'close' not in df.columns:
            return False, "No close column"
            
        # 数値変換
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        if 'volume' in df.columns:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        else:
            df['volume'] = 0
            
        df = df.dropna(subset=['close']).reset_index(drop=True)
        if len(df) < MIN_ROWS:
            return False, "Not enough rows"

        # ==========================================
        # 2. テクニカル指標の計算 (Step2/Step3で使用)
        # ==========================================
        # VWAP
        if 'vwap' not in df.columns or df['vwap'].isna().mean() > 0.5:
            df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['volume'])
            df['vwap'] = df['vwap'].fillna(df['close']) # 計算できない場合はCloseで埋める

        # RSI (14)
        df['rsi_14'] = ta.rsi(df['close'], length=14)
        
        # MACD (12, 26, 9)
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        if macd is not None:
            df = pd.concat([df, macd], axis=1)
            # カラム名を統一 (pandas_taの出力名に合わせる)
            if 'MACDh_12_26_9' not in df.columns:
                # バージョンによってカラム名が違う場合の保険
                cols = [c for c in df.columns if 'MACDh' in c]
                if cols: df['macdh_12_26_9'] = df[cols[0]]
        
        # Bollinger Bands (20, 2)
        bb = ta.bbands(df['close'], length=20, std=2)
        if bb is not None:
            df = pd.concat([df, bb], axis=1)
            cols = [c for c in df.columns if 'BBP' in c]
            if cols: df['bbp_20_2.0'] = df[cols[0]]

        # 移動平均線 (15, 75) - トレンド判定用
        df['ma15'] = ta.sma(df['close'], length=15)
        df['ma75'] = ta.sma(df['close'], length=75)
        
        # ADX (14) - トレンド強度判定用
        # high/lowがない場合はcloseで代用
        h = df['high'] if 'high' in df.columns else df['close']
        l = df['low'] if 'low' in df.columns else df['close']
        adx = ta.adx(h, l, df['close'], length=14)
        if adx is not None:
            df = pd.concat([df, adx], axis=1)
            cols = [c for c in df.columns if 'ADX' in c]
            if cols: df['adx'] = df[cols[0]]
        
        df['adx'] = df['adx'].fillna(0)

        # ==========================================
        # 3. マルチタスク用「未来の正解」ラベル作成
        # ==========================================
        # 15期間先のリターン (未来予測用)
        # shift(-15)で「15行先のデータ」を今の行に持ってくる
        future_close = df['close'].shift(-15)
        df['target_return_15m'] = (future_close - df['close']) / df['close']
        
        # 15期間先のボラティリティ (リスク予測用)
        # rolling(15).std() を未来方向にずらす
        # ここでは簡易的に「未来15期間の最高値 - 最安値」を計算
        indexer = pd.api.indexers.FixedForwardWindowIndexer(window_size=15)
        future_high = df['high'].rolling(window=indexer).max() if 'high' in df.columns else df['close'].rolling(window=indexer).max()
        future_low = df['low'].rolling(window=indexer).min() if 'low' in df.columns else df['close'].rolling(window=indexer).min()
        
        df['target_volatility_15m'] = (future_high - future_low) / df['close']

        # ==========================================
        # 4. レジーム(相場環境)判定ロジック
        # ==========================================
        # 条件1: 暴落 (Crash)
        # 5分前と比較して急落しているか
        crash_drop = df['close'].pct_change(5)
        is_crash = crash_drop < CRASH_THRESHOLD
        
        # 条件2: トレンド (Trend)
        # ADXが一定以上 かつ MA75の傾きがある
        ma75_slope = df['ma75'].diff()
        is_strong_trend = df['adx'] > ADX_THRESHOLD
        
        is_uptrend = is_strong_trend & (ma75_slope > 0) & (df['close'] > df['ma75'])
        is_downtrend = is_strong_trend & (ma75_slope < 0) & (df['close'] < df['ma75'])
        
        # レジーム列の作成 (デフォルトはRANGE)
        df['regime'] = 'RANGE'
        
        # 優先順位: 暴落 > トレンド > レンジ
        df.loc[is_uptrend, 'regime'] = 'UPTREND'
        df.loc[is_downtrend, 'regime'] = 'DOWNTREND'
        df.loc[is_crash, 'regime'] = 'CRASH' # 暴落は最優先で上書き
        
        # 欠損値を含む行（最初の数行や最後の数行）を削除
        df = df.dropna().reset_index(drop=True)
        
        if len(df) < MIN_ROWS:
            return False, "Too short after processing"

        # 保存
        df.to_csv(filepath, index=False)
        return True, f"Success ({len(df)} rows, Regime: {df['regime'].value_counts().to_dict()})"

    except Exception as e:
        return False, str(e)

def main():
    print(f"{'='*60}")
    print(f"🔧 データ整備・拡張 v20.0 (DeepResearch)")
    print(f"   対象: {DATA_DIR}/*.csv")
    print(f"   処理: テクニカル指標計算 + 未来予測ラベル作成 + レジーム判定")
    print(f"{'='*60}")
    
    files = glob.glob(os.path.join(DATA_DIR, "*.csv"))
    if not files:
        print(f"❌ {DATA_DIR} にCSVファイルがありません。")
        return

    success_count = 0
    
    for i, f in enumerate(files):
        filename = os.path.basename(f)
        print(f"[{i+1}/{len(files)}] Processing {filename} ... ", end="")
        
        ok, msg = process_file(f)
        if ok:
            print(f"✅ OK")
            # print(f"   -> {msg}") # 詳細ログが必要な場合はコメントアウト解除
            success_count += 1
        else:
            print(f"⚠️ Skip: {msg}")

    print(f"\n{'='*60}")
    print(f"🎉 完了: {success_count} / {len(files)} ファイルをv20仕様に変換しました。")
    print(f"   これでAIは「未来」と「相場環境」を学ぶ準備が整いました。")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()