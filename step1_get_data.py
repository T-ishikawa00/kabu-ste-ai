# -*- coding: utf-8 -*-
"""
step1_setup_data.py
===================
yfinanceを使って、新ポートフォリオ全銘柄の過去データを取得し、
ボットが学習できる形式 (merged_XXXX.csv) に変換して保存する。
"""

import os
import yfinance as yf
import pandas as pd
import time

# 保存先ディレクトリ
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ★v12.0 Aggressive Portfolio (全16銘柄)
TARGET_CODES = [
    # Tier 1: 重工・造船
    "7011", "7013", "7003", "6269",
    # Tier 2: 半導体エース
    "285A", "3110",
    # Tier 3: 監視枠 (データは必要)
    "6920", "6146",
    # Tier 4: 主力・ディフェンシブ
    "6954", "6506", "9984", "8411", "9107", "9501", "8136", "7974"
]

def download_and_save(code):
    print(f"📥 [{code}] データを取得中...", end=" ")
    
    ticker = f"{code}.T"
    try:
        # 過去5日間の1分足を取得
        df = yf.download(ticker, period="5d", interval="1m", progress=False)
        
        if len(df) == 0:
            print("❌ データなし")
            return

        # マルチインデックス解除 (yfinanceの仕様対策)
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df.columns = df.columns.get_level_values(0)
            except:
                pass
        
        # カラム名を小文字に統一
        df.columns = [c.lower() for c in df.columns]
        
        # 必要なカラムに絞る (テクニカル計算は step4 に任せるのでRawデータのみ)
        required = ['open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required):
            print("⚠️ カラム不足")
            return
            
        df = df[required].dropna()
        
        # タイムゾーン調整 (UTC -> JST)
        # yfinanceは通常ローカル時刻かUTCで返すが、念のため
        if df.index.tz is not None:
            df.index = df.index.tz_convert('Asia/Tokyo')
        else:
            # tzなしの場合はUTCとみなしてJSTへ (簡易対応)
            df.index = df.index + pd.Timedelta(hours=9)

        # ファイル保存 (merged_{code}.csv)
        save_path = os.path.join(DATA_DIR, f"merged_{code}.csv")
        df.to_csv(save_path)
        
        print(f"✅ 保存完了 ({len(df)}行)")
        
    except Exception as e:
        print(f"❌ エラー: {e}")

def main():
    print("=" * 60)
    print("🚀 初期データセットアップ (yfinance)")
    print("=" * 60)
    
    for code in TARGET_CODES:
        download_and_save(code)
        time.sleep(1) # サーバー負荷軽減
        
    print("-" * 60)
    print("🎉 全データの取得が完了しました。")
    print("👉 次の手順:")
    print("   1. py step4_process_daily_log.py  (テクニカル指標の計算)")
    print("   2. py step3_train.py              (AIモデルの学習 v12)")
    print("=" * 60)

if __name__ == "__main__":
    main()