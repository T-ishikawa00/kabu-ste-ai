# -*- coding: utf-8 -*-
"""
debug_check_data.py
===================
データが正しく取れているか確認
"""

import os
import glob
import pandas as pd
import requests

# J-Quants API
MY_API_KEY = "REDACTED"
BASE_URL = "https://api.jquants.com/v2"
DATA_DIR = "data_archive_daily"

def check_api_response():
    """APIレスポンスの生データを確認"""
    print("=" * 60)
    print("🔍 J-Quants API レスポンス確認")
    print("=" * 60)
    
    url = f"{BASE_URL}/equities/bars/daily"
    params = {"date": "20260116"}
    headers = {"x-api-key": MY_API_KEY}
    
    res = requests.get(url, headers=headers, params=params)
    
    print(f"Status: {res.status_code}")
    
    if res.status_code == 200:
        data = res.json()
        
        # キー確認
        print(f"\nレスポンスのキー: {list(data.keys())}")
        
        # データ取得
        if "daily_quotes" in data:
            quotes = data["daily_quotes"]
        elif "lists" in data:
            quotes = data["lists"]
        elif "data" in data:
            quotes = data["data"]
        else:
            quotes = data if isinstance(data, list) else []
        
        print(f"データ件数: {len(quotes)}")
        
        if quotes:
            # 1件目のデータを表示
            print(f"\n【1件目のデータ（生）】")
            sample = quotes[0]
            for k, v in sample.items():
                print(f"  {k}: {v} ({type(v).__name__})")
        else:
            print("⚠️ データが空です")
    else:
        print(f"❌ エラー: {res.text}")


def check_saved_files():
    """保存済みCSVの中身を確認"""
    print("\n" + "=" * 60)
    print("🔍 保存済みCSVファイル確認")
    print("=" * 60)
    
    files = glob.glob(os.path.join(DATA_DIR, "daily_*.csv"))
    print(f"ファイル数: {len(files)}")
    
    if not files:
        print("❌ ファイルがありません")
        return
    
    # ランダムに3件確認
    import random
    samples = random.sample(files, min(3, len(files)))
    
    for fp in samples:
        print(f"\n【{os.path.basename(fp)}】")
        try:
            df = pd.read_csv(fp)
            print(f"  行数: {len(df)}")
            print(f"  カラム: {list(df.columns)}")
            if len(df) > 0:
                print(f"  最新行:")
                latest = df.iloc[-1]
                for col in df.columns:
                    print(f"    {col}: {latest[col]}")
        except Exception as e:
            print(f"  ❌ 読み込みエラー: {e}")


def check_screening_sample():
    """スクリーニングに使う条件をサンプルで確認"""
    print("\n" + "=" * 60)
    print("🔍 スクリーニング条件テスト")
    print("=" * 60)
    
    files = glob.glob(os.path.join(DATA_DIR, "daily_*.csv"))
    
    if not files:
        print("❌ ファイルがありません")
        return
    
    # 7011（三菱重工）をテスト
    test_file = os.path.join(DATA_DIR, "daily_7011.csv")
    if not os.path.exists(test_file):
        test_file = files[0]
    
    print(f"テストファイル: {test_file}")
    
    df = pd.read_csv(test_file, parse_dates=['datetime'])
    print(f"行数: {len(df)}")
    
    if len(df) < 30:
        print("❌ データが30日分未満")
        return
    
    df.sort_values('datetime', inplace=True)
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    print(f"\n【最新日のデータ】")
    print(f"  日付: {latest['datetime']}")
    print(f"  始値: {latest['open']}")
    print(f"  高値: {latest['high']}")
    print(f"  安値: {latest['low']}")
    print(f"  終値: {latest['close']}")
    print(f"  出来高: {latest['volume']}")
    
    # 計算テスト
    close = float(latest['close'])
    prev_close = float(prev['close'])
    volume = float(latest['volume'])
    
    print(f"\n【計算テスト】")
    print(f"  売買代金: {close * volume:,.0f} 円")
    print(f"  変化率: {(close - prev_close) / prev_close * 100:.2f}%")
    
    # MA25
    ma25 = df['close'].rolling(25).mean().iloc[-1]
    print(f"  25日MA: {ma25:.0f}")
    print(f"  乖離率: {(close - ma25) / ma25 * 100:.2f}%")
    
    # 条件チェック
    print(f"\n【条件チェック】")
    print(f"  価格150-5000: {150 <= close <= 5000}")
    print(f"  売買代金>3億: {close * volume > 300_000_000}")
    print(f"  上昇トレンド(close>MA25): {close > ma25}")


if __name__ == "__main__":
    check_api_response()
    check_saved_files()
    check_screening_sample()