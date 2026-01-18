# -*- coding: utf-8 -*-
"""
check_screening.py
==================
Step 2 で銘柄がヒットしない理由を診断するツール
"""

import os
import pandas as pd
import glob

# 設定（Step 2と同じにする）
DATA_DIR = "data_archive_daily"
VOLUME_MULTIPLIER = 1.5  # 1.5倍の設定でテスト

def check_stock(code):
    file_path = os.path.join(DATA_DIR, f"daily_{code}.csv")
    if not os.path.exists(file_path):
        print(f"❌ {code}: データファイルがありません")
        return

    try:
        df = pd.read_csv(file_path, parse_dates=['datetime'])
        if len(df) < 30:
            print(f"❌ {code}: データ不足 ({len(df)}日分)")
            return

        df.sort_values('datetime', inplace=True)
        latest = df.iloc[-1]
        
        # 値の取得
        price = float(latest['close'])
        vol = float(latest['volume'])
        
        # 出来高平均（直近5日）
        recent_vol = df['volume'].iloc[-6:-1]
        avg_vol = recent_vol.mean()
        vol_ratio = vol / (avg_vol + 1)
        
        # 移動平均
        ma25 = df['close'].rolling(25).mean().iloc[-1]
        
        # 判定
        print(f"📊 銘柄 {code} の診断結果 ({latest['datetime'].strftime('%Y-%m-%d')})")
        print(f"   現在値: {price:,.0f} 円")
        print(f"   出来高: {vol:,.0f} (平均: {avg_vol:,.0f}) -> 倍率 {vol_ratio:.2f}倍")
        
        reasons = []
        if not (100 <= price <= 5000):
            reasons.append(f"株価対象外 (条件: 100-5000)")
        
        if vol_ratio < VOLUME_MULTIPLIER:
            reasons.append(f"出来高不足 (現在{vol_ratio:.2f}倍 < 条件{VOLUME_MULTIPLIER}倍)")
            
        if price < ma25:
             reasons.append(f"トレンド下降中 (株価 < 25日線)")
             
        if price <= float(latest['open']):
             reasons.append(f"陰線で終了 (始値より安い)")

        if not reasons:
            print("   ✅ 合格！ (なぜヒットしなかったのか謎です)")
        else:
            print("   ❌ 不合格の理由:")
            for r in reasons:
                print(f"      - {r}")
        print("-" * 40)

    except Exception as e:
        print(f"⚠️ エラー: {e}")

# Configに残っている銘柄と、有名銘柄をチェック
codes_to_check = ["6167", "7371", "4406", "7203", "9984"] # 7203:トヨタ, 9984:SBG

print("="*60)
print("🔍 スクリーニング診断")
print("="*60)
for c in codes_to_check:
    check_stock(c)