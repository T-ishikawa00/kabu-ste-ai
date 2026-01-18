# -*- coding: utf-8 -*-
"""
tool_normalize_data_raw.py
==========================
data_raw フォルダの中身を整理・正規化するツール

【機能】
1. 5桁コード(xxxxx0)のファイルを4桁(xxxxx)にリネーム
2. 重複ファイルがある場合は、行数が多い方を残す
3. 明らかに行数が少ないゴミファイル(30行未満)を削除
"""

import os
import glob
import pandas as pd

# 設定
TARGET_DIR = "data_raw"
MIN_ROWS = 30  # これ未満の行数のファイルは削除

def get_line_count(filepath):
    """ファイルの行数を高速に数える"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except:
        return 0

def main():
    print("=" * 60)
    print("🧹 data_raw 正規化・お掃除ツール")
    print("=" * 60)
    
    if not os.path.exists(TARGET_DIR):
        print(f"❌ {TARGET_DIR} が見つかりません")
        return

    # 1. 5桁ファイル(xxxxx0.csv)を探す
    files_5digit = glob.glob(os.path.join(TARGET_DIR, "*0.csv"))
    print(f"🔍 5桁ファイルの検出数: {len(files_5digit)}")
    
    renamed_count = 0
    deleted_count = 0
    
    for path_5 in files_5digit:
        filename_5 = os.path.basename(path_5)
        # daily_ を除去してコード抽出
        code_part = filename_5.replace("daily_", "").replace(".csv", "")
        
        # 本当に5桁+0か確認（例: 13010 -> OK, 5803 -> NG）
        if len(code_part) == 5 and code_part.endswith("0"):
            code_4 = code_part[:-1]
            filename_4 = f"daily_{code_4}.csv"
            path_4 = os.path.join(TARGET_DIR, filename_4)
            
            # 4桁版がすでに存在するか？
            if os.path.exists(path_4):
                # 重複！ 行数勝負
                lines_5 = get_line_count(path_5)
                lines_4 = get_line_count(path_4)
                
                if lines_5 > lines_4:
                    # 5桁の方が立派 -> 4桁を消して5桁をリネーム
                    try:
                        os.remove(path_4)
                        os.rename(path_5, path_4)
                        print(f"   🔄 入れ替え: {code_5} (大) -> {code_4}")
                        renamed_count += 1
                    except Exception as e:
                        print(f"   ❌ エラー: {e}")
                else:
                    # 4桁の方が立派 -> 5桁を捨てる
                    try:
                        os.remove(path_5)
                        print(f"   🗑️ 削除(重複): {filename_5}")
                        deleted_count += 1
                    except Exception as e:
                        print(f"   ❌ エラー: {e}")
            else:
                # 重複なし -> 単純リネーム
                try:
                    os.rename(path_5, path_4)
                    print(f"   ✏️ リネーム: {filename_5} -> {filename_4}")
                    renamed_count += 1
                except Exception as e:
                    print(f"   ❌ リネーム失敗: {e}")
    
    # 2. ゴミファイル削除（行数が少なすぎるやつ）
    print("-" * 60)
    print("🔍 ゴミファイル（データ不足）の最終チェック...")
    
    all_files = glob.glob(os.path.join(TARGET_DIR, "daily_*.csv"))
    small_files_deleted = 0
    
    for f in all_files:
        lines = get_line_count(f)
        if lines < MIN_ROWS:
            try:
                os.remove(f)
                print(f"   🗑️ 削除(データ不足): {os.path.basename(f)} ({lines}行)")
                small_files_deleted += 1
            except:
                pass

    print("=" * 60)
    print("🎉 完了しました")
    print(f"   リネーム: {renamed_count}")
    print(f"   重複削除: {deleted_count}")
    print(f"   ゴミ掃除: {small_files_deleted}")
    print("=" * 60)

if __name__ == "__main__":
    main()