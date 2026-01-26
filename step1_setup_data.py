# -*- coding: utf-8 -*-
"""
step0_yfinance_patch.py
=======================
【最終兵器】
J-Quantsにも実戦ログにもなかった「完全な欠落銘柄（485Aなど）」を
yfinanceを使って穴埋めします。

★安全設計:
すでにデータがある銘柄（285Aなど）はスキップします。
上書き事故を防ぎます。
"""

import os
import yfinance as yf
import pandas as pd
import time

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# 監視対象リスト (ここにある中で、ファイルがないものを探します)
TARGET_CODES = [
    "9501", "8411", "5803", "9984", "6954", "6269", "285A", "5801", "485A", "4570",
    "3110", "7974", "6525", "6330", "1662", "3350", "7746", "5016", "8306", "4506",
    "1893", "278A", "6702", "464A", "5101", "6501", "6758", "7267", "3382", "4446",
    "4082", "7003", "7735", "7711", "8135", "186A", "5802", "9107", "7453", "9227",
    "7701", "8086", "9104", "4889", "4901", "6770", "9830", "2503"
]

def download_and_save(code):
    file_path = os.path.join(DATA_DIR, f"merged_{code}.csv")
    
    # ★すでにデータがあるなら何もしない（重要）
    if os.path.exists(file_path):
        print(f"🛡️  [{code}] はすでにデータがあります (Skip)")
        return

    print(f"📥 [{code}] データをyfinanceから取得中...", end=" ")
    
    ticker = f"{code}.T"
    try:
        # 1分足を最大期間（約7日分）取得
        # ※足りなければ daily データに切り替える戦略もありですが、
        #   ボットの性質上、短い期間でも 1分足 の方が食いつきが良いです。
        df = yf.download(ticker, period="5d", interval="1m", progress=False)
        
        if df.empty:
            print("❌ データなし")
            return

        # マルチインデックス解除
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
        # カラム名を統一 (Open -> open)
        df.columns = [c.lower() for c in df.columns]
        
        # 必要なカラム確認
        required = ['open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required):
            print("⚠️ カラム不足")
            return
            
        # 整形
        df = df[required].dropna()
        df.index.name = "datetime"
        
        # タイムゾーン調整 (UTC -> JST)
        if df.index.tz is not None:
            df.index = df.index.tz_convert('Asia/Tokyo')
        else:
            df.index = df.index + pd.Timedelta(hours=9)
            
        # indexを列に戻す（保存形式を合わせるため）
        df.reset_index(inplace=True)

        # 保存
        df.to_csv(file_path, index=False)
        print(f"✅ 保存完了 ({len(df)}行)")
        
    except Exception as e:
        print(f"❌ エラー: {e}")

def main():
    print("=" * 60)
    print("🚑 yfinance データ補完ミッション")
    print("   不足している銘柄のみをダウンロードします")
    print("=" * 60)
    
    for code in TARGET_CODES:
        download_and_save(str(code))
        time.sleep(0.5) # 優しさウェイト
        
    print("-" * 60)
    print("🎉 補完完了！ step3_train.py に進んでください。")
    print("=" * 60)

if __name__ == "__main__":
    main()