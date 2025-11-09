# -*- coding: utf-8 -*-
"""
fetch_ohlcv_yahoo.py
Yahoo!ファイナンスからCore5銘柄の1分足データを取得（直近7日）
"""

import os
import yfinance as yf
import pandas as pd

OUT_DIR = "./data/ohlcv_1min"
PERIOD = "7d"        # ✅ 7日間に変更
INTERVAL = "1m"      # ✅ 1分足
symbols = {
    "7013": "7013.T",  # IHI
    "7011": "7011.T",  # 三菱重工
    "5803": "5803.T",  # フジクラ
    "9432": "9432.T",  # NTT
    "5020": "5020.T",  # ENEOS
}

os.makedirs(OUT_DIR, exist_ok=True)

print("📊 Yahoo!ファイナンスから直近7日分の1分足データを取得中...\n")

for code, ticker in symbols.items():
    print(f"▶ {code} ({ticker}) 取得中...")
    try:
        df = yf.download(ticker, period=PERIOD, interval=INTERVAL, progress=False)
        if df.empty:
            print(f"⚠️ {code}: データなし（夜間や休日かも）")
            continue

        df = df.reset_index()
        df.rename(columns={
            "Datetime": "ts",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume"
        }, inplace=True)
        df["ts"] = df["ts"].dt.tz_localize(None)
        df = df[["ts", "open", "high", "low", "close", "volume"]]

        out_path = os.path.join(OUT_DIR, f"{code}.csv")
        df.to_csv(out_path, index=False)
        print(f"✅ 保存完了: {out_path} ({len(df)}行)")

    except Exception as e:
        print(f"❌ {code}: 取得失敗 - {e}")

print("\n🎯 全銘柄のデータ取得が完了しました。")
print("→ data/ohlcv_1min/ フォルダにCSVが生成されています。")
