# -*- coding: utf-8 -*-
"""
utils_import_archive.py v17.1 (Alpha-Num Fix)
=============================================
data_archive_daily から data へデータをコピーするツール。

【修正点】
- 銘柄コードの判定を `isdigit()` (数字のみ) から `isalnum()` (英数字OK) に変更。
- これにより、485A, 285A などの英字入りコードも正しくコピーされます。
"""

import os
import glob
import shutil

# ==========================================
# 設定
# ==========================================
SOURCE_DIR = "data_archive_daily"  # 宝の山（コピー元）
DEST_DIR = "data"                  # AIの現場（コピー先）

def main():
    print("=" * 60)
    print("🚚 データ高速引っ越しツール (英数字対応版)")
    print(f"   From: {SOURCE_DIR}")
    print(f"   To:   {DEST_DIR}")
    print("=" * 60)

    if not os.path.exists(SOURCE_DIR):
        print(f"❌ コピー元 ({SOURCE_DIR}) が見つかりません！")
        return

    os.makedirs(DEST_DIR, exist_ok=True)

    source_files = glob.glob(os.path.join(SOURCE_DIR, "daily_*.csv"))
    if not source_files:
        print("⚠️ コピー元のファイルが見つかりません")
        return

    print(f"📦 {len(source_files)} 個のファイルが見つかりました。")
    print("   コピーを開始します...")

    count = 0
    skip = 0

    for src_path in source_files:
        try:
            filename = os.path.basename(src_path)
            
            # daily_485A.csv -> 485A
            code = filename.replace("daily_", "").replace(".csv", "")
            
            # ★修正: 英数字OKにする (isalnum)
            if len(code) != 4 or not code.isalnum():
                continue

            # 大文字に統一 (485a -> 485A)
            code = code.upper()

            # 新しい名前
            new_filename = f"merged_{code}.csv"
            dest_path = os.path.join(DEST_DIR, new_filename)

            # すでに data にあるならスキップ（上書きしたい場合はここをコメントアウト）
            if os.path.exists(dest_path):
                skip += 1
                continue

            shutil.copy2(src_path, dest_path)
            count += 1
            
            if count % 500 == 0:
                print(f"   🚀 {count} ファイル処理完了...", end="\r")

        except Exception as e:
            print(f"❌ Error {filename}: {e}")

    print("\n" + "=" * 60)
    print(f"🎉 引っ越し完了！")
    print(f"   ✅ コピー成功: {count} 件")
    print(f"   ⏩ スキップ  : {skip} 件 (すでに存在)")
    print("=" * 60)
    print("👉 これで 485A なども認識されます。step3_train.py を再実行してください。")

if __name__ == "__main__":
    main()