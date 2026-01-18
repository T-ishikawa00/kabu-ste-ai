# -*- coding: utf-8 -*-
"""
utils_compile_raw.py
====================
【実戦データ活用ツール】
data_raw フォルダ（ボットの実戦ログ置き場）にあるバラバラのCSVを、
銘柄ごとに「一本の時系列データ」に結合して、AI用の data フォルダに配備します。

メリット:
1. API制限を気にせず、手持ちのデータで即座に学習可能。
2. 285Aなど、J-Quantsで取れなかった銘柄も、実戦ログがあれば復元可能。
"""

import os
import glob
import pandas as pd
import shutil

# ==========================================
# 設定
# ==========================================
SOURCE_DIR = "data_raw"  # 実戦ログの場所
DEST_DIR = "data"        # AIが読む場所

def normalize_columns(df):
    """カラム名をAIが読める形式に統一"""
    # 一般的なカラム名のバリエーション
    col_map = {
        "Date": "datetime", "date": "datetime", "timestamp": "datetime", "Timestamp": "datetime",
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume",
        "current_price": "close", "price": "close", "vol": "volume"
    }
    df = df.rename(columns=col_map)
    
    # 必要なカラムが揃っているか確認
    required = ["datetime", "open", "high", "low", "close", "volume"]
    
    # 足りない場合は補完（closeで埋めるなど）
    if "close" in df.columns:
        for c in ["open", "high", "low"]:
            if c not in df.columns:
                df[c] = df["close"]
        if "volume" not in df.columns:
            df["volume"] = 0
            
    # 最終チェック
    if all(c in df.columns for c in required):
        return df[required]
    return None

def main():
    print("=" * 60)
    print("🏭 実戦ログ結合ツール (Raw -> Merged)")
    print("   data_raw のデータを結合して data フォルダに配備します")
    print("=" * 60)

    if not os.path.exists(SOURCE_DIR):
        print(f"❌ {SOURCE_DIR} が見つかりません！")
        return

    os.makedirs(DEST_DIR, exist_ok=True)

    # 1. 全CSVファイルをスキャン
    print(f"🔍 {SOURCE_DIR} 内をスキャン中...")
    all_files = glob.glob(os.path.join(SOURCE_DIR, "**", "*.csv"), recursive=True)
    
    if not all_files:
        print("⚠️ CSVファイルが見つかりませんでした。")
        return

    print(f"   -> {len(all_files)} 個のファイルを発見。")

    # 2. 銘柄ごとにファイルをグループ化
    stock_files = {}
    
    for filepath in all_files:
        filename = os.path.basename(filepath)
        
        # ファイル名からコードを推測 (例: 285A.csv, 285A_20240101.csv)
        # 英数字4文字のパターンを探す簡易ロジック
        try:
            # 拡張子を除去
            base = filename.replace(".csv", "")
            # "daily_" などを除去
            base = base.replace("daily_", "").replace("merged_", "")
            
            # 先頭の英数字部分をコードとする (285A_log -> 285A)
            code_candidate = base.split("_")[0]
            
            if len(code_candidate) >= 4 and code_candidate.isalnum():
                code = code_candidate.upper()
                if code not in stock_files:
                    stock_files[code] = []
                stock_files[code].append(filepath)
        except:
            continue

    print(f"   -> {len(stock_files)} 銘柄のデータを検出しました。")
    print("-" * 60)

    # 3. 結合して保存
    success_count = 0
    
    for code, files in stock_files.items():
        try:
            # 複数のCSVを読み込んで連結
            df_list = []
            for f in files:
                try:
                    tmp = pd.read_csv(f)
                    norm_df = normalize_columns(tmp)
                    if norm_df is not None:
                        df_list.append(norm_df)
                except:
                    pass
            
            if not df_list:
                continue
                
            # 結合
            full_df = pd.concat(df_list)
            
            # 日付でソートして重複排除
            full_df["datetime"] = pd.to_datetime(full_df["datetime"])
            full_df = full_df.sort_values("datetime")
            full_df = full_df.drop_duplicates(subset=["datetime"], keep="last")
            
            # データ数が少なすぎる場合はスキップ（ノイズ除去）
            if len(full_df) < 10:
                continue

            # 保存
            dest_path = os.path.join(DEST_DIR, f"merged_{code}.csv")
            full_df.to_csv(dest_path, index=False)
            
            print(f"✅ {code}: {len(full_df)}行 のデータを生成しました")
            success_count += 1
            
        except Exception as e:
            print(f"⚠️ Error {code}: {e}")

    print("\n" + "=" * 60)
    print(f"🎉 処理完了！")
    print(f"   生成ファイル: {success_count} 件")
    print(f"   保存先: {DEST_DIR}")
    print("=" * 60)
    print("👉 これで step3_train.py を実行してください。")
    print("   285A などの実戦データを使って学習できます！")

if __name__ == "__main__":
    main()