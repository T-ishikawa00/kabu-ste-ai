# -*- coding: utf-8 -*-
"""
step2_screening.py v2.0
=======================
J-Quants日足データを分析し、「明日監視すべき銘柄リスト」を作成

【スクリーニング条件】
1. 出来高急増（直近5日平均の3倍以上）
2. 上昇トレンド（25日移動平均線より上）
3. 陽線で終わっている（終値 > 始値）
4. 価格帯フィルター（100円〜5000円）
5. ETF・REITを除外
"""

import os
import glob
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings('ignore')

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data_archive_daily"          # 日足データの場所
OUTPUT_FILE = "target_list.csv"  # 結果の保存先

# スクリーニング条件
MIN_PRICE = 100           # 最低株価
MAX_PRICE = 5000          # 最高株価
VOLUME_MULTIPLIER = 3.0   # 出来高急増倍率（3倍）
MIN_DATA_DAYS = 30        # 最低必要データ日数

# 除外キーワード（銘柄名に含まれていたら除外）
EXCLUDE_KEYWORDS = [
    "ETF", "ＥＴＦ", "投信", "インデックス", "ファンド",
    "REIT", "リート", "ＲＥＩＴ", "債券"
]


def analyze_stock(file_path):
    """
    1銘柄を分析し、条件に合えば結果を返す
    """
    try:
        # CSV読み込み
        df = pd.read_csv(file_path, parse_dates=['datetime'])
        df.sort_values('datetime', inplace=True)
        df.reset_index(drop=True, inplace=True)
        
        # データ量チェック
        if len(df) < MIN_DATA_DAYS:
            return None
        
        # 最新データ
        latest = df.iloc[-1]
        
        # 銘柄コード取得
        filename = os.path.basename(file_path)
        code = filename.replace("daily_", "").replace(".csv", "")
        
        # ─────────────────────────────────────────
        # フィルター1: コード形式（4桁数字のみ）
        # ─────────────────────────────────────────
        if len(code) != 4 or not code.isdigit():
            return None
        
        # ─────────────────────────────────────────
        # フィルター2: 価格帯
        # ─────────────────────────────────────────
        close_price = float(latest['close'])
        if not (MIN_PRICE <= close_price <= MAX_PRICE):
            return None
        
        # ─────────────────────────────────────────
        # フィルター3: 25日移動平均線（上昇トレンド）
        # ─────────────────────────────────────────
        df['ma25'] = df['close'].rolling(window=25).mean()
        ma25 = df['ma25'].iloc[-1]
        
        if pd.isna(ma25):
            return None
        
        if close_price < ma25:
            return None  # 下降トレンドは除外
        
        # ─────────────────────────────────────────
        # フィルター4: 出来高急増
        # ─────────────────────────────────────────
        # 直近5日の平均出来高（当日除く）
        recent_vol = df['volume'].iloc[-6:-1]
        avg_volume = recent_vol.mean()
        
        if avg_volume < 10000:
            return None  # 出来高が少なすぎる銘柄は除外
        
        today_volume = float(latest['volume'])
        volume_ratio = today_volume / avg_volume
        
        if volume_ratio < VOLUME_MULTIPLIER:
            return None  # 出来高急増してない
        
        # ─────────────────────────────────────────
        # フィルター5: 陽線判定（終値 > 始値）
        # ─────────────────────────────────────────
        open_price = float(latest['open'])
        
        if close_price <= open_price:
            return None  # 陰線は除外
        
        # ─────────────────────────────────────────
        # 追加指標の計算
        # ─────────────────────────────────────────
        # 変化率
        prev_close = float(df['close'].iloc[-2])
        change_pct = (close_price - prev_close) / prev_close * 100
        
        # MA25との乖離率
        ma_distance = (close_price - ma25) / ma25 * 100
        
        return {
            "code": code,
            "close": close_price,
            "change_pct": round(change_pct, 2),
            "volume": int(today_volume),
            "vol_ratio": round(volume_ratio, 2),
            "ma_dist": round(ma_distance, 2),
            "score": round(volume_ratio * (1 + change_pct/100), 2)  # 総合スコア
        }
    
    except Exception as e:
        return None


def main():
    print("=" * 60)
    print("🔍 銘柄スクリーニング v2.0 (J-Quants版)")
    print("=" * 60)
    print(f"📋 条件:")
    print(f"   価格帯: {MIN_PRICE}円 〜 {MAX_PRICE}円")
    print(f"   出来高: 直近平均の {VOLUME_MULTIPLIER}倍以上")
    print(f"   トレンド: 25日MA より上")
    print(f"   ローソク: 陽線")
    print("-" * 60)
    
    # データファイル取得
    files = glob.glob(os.path.join(DATA_DIR, "daily_*.csv"))
    
    if not files:
        print("❌ データがありません")
        print("   先に step1_jquants_daily.py を実行してください")
        return
    
    print(f"📊 分析対象: {len(files)} 銘柄")
    
    results = []
    
    # 全銘柄を分析
    for i, file_path in enumerate(files):
        if i % 500 == 0:
            print(f"   処理中... [{i}/{len(files)}]", end="\r")
        
        result = analyze_stock(file_path)
        if result:
            results.append(result)
    
    print(f"   処理中... [{len(files)}/{len(files)}] 完了！")
    print("-" * 60)
    
    # 結果の表示
    if results:
        df_result = pd.DataFrame(results)
        
        # スコア順にソート
        df_result.sort_values(by="score", ascending=False, inplace=True)
        df_result.reset_index(drop=True, inplace=True)
        
        print(f"\n🎯 {len(df_result)} 銘柄が条件にマッチ！\n")
        
        # 上位10件を表示
        print(f"{'順位':<4} {'コード':<6} {'終値':>8} {'変化率':>8} {'出来高倍率':>10} {'スコア':>8}")
        print("-" * 60)
        
        for i, row in df_result.head(10).iterrows():
            print(f"{i+1:<4} {row['code']:<6} {row['close']:>8,.0f} {row['change_pct']:>+7.2f}% {row['vol_ratio']:>10.1f}x {row['score']:>8.2f}")
        
        # CSV保存
        df_result.to_csv(OUTPUT_FILE, index=False)
        print(f"\n💾 保存しました: {OUTPUT_FILE}")
        
    else:
        print("🍂 条件に合う銘柄が見つかりませんでした")
        print("   条件を緩めるか、相場が落ち着いている日かもしれません")
        
        # 空のCSVを作成（エラー防止）
        pd.DataFrame(columns=["code", "close", "change_pct", "volume", "vol_ratio", "ma_dist", "score"]).to_csv(OUTPUT_FILE, index=False)
    
    print("\n" + "=" * 60)
    print("👉 次: py -3.12 step6_inject_config.py")
    print("=" * 60)


if __name__ == "__main__":
    main()