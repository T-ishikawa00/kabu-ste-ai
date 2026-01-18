# -*- coding: utf-8 -*-
"""
tool_select_to_raw.py v2.0
==========================
倉庫(data_archive_daily)から、「ランダムに100銘柄」だけを厳選して
加工場(data_raw)にコピーするツール。

【特徴】
・ご提示いただいたロジックを参考に、MAX_SAMPLES = 100 で制限
・実行するたびに data_raw をリセットするのでゴミが溜まりません
・5桁コード(xxxxx0) は自動で 4桁(xxxxx) に直してコピーします
"""

import os
import glob
import shutil
import random

# 設定
SRC_DIR = "data_archive_daily"  # 倉庫（コピー元）
DST_DIR = "data_raw"            # 加工場（コピー先）
MIN_ROWS = 300                  # 最低行数（これ以下のゴミは無視）
MAX_SAMPLES = 100               # ★ここで100銘柄に制限！

def get_line_count(filepath):
    """行数を高速に数える"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except:
        return 0

def main():
    print("=" * 60)
    print("📦 100銘柄厳選 & 引越しツール v2.0")
    print(f"   倉庫 -> {DST_DIR} へ ランダムに{MAX_SAMPLES}銘柄 をコピー")
    print("=" * 60)
    
    # 1. 倉庫チェック
    if not os.path.exists(SRC_DIR):
        print(f"❌ {SRC_DIR} がありません。")
        return

    # 2. data_raw をリセット（前のファイルを全削除して綺麗にする）
    if os.path.exists(DST_DIR):
        print(f"🧹 {DST_DIR} を掃除中...", end="")
        shutil.rmtree(DST_DIR)
        print("完了")
    os.makedirs(DST_DIR, exist_ok=True)
    
    # 3. 倉庫のファイル一覧を取得
    files = glob.glob(os.path.join(SRC_DIR, "daily_*.csv"))
    print(f"📚 倉庫の在庫: {len(files)} ファイル")
    
    # 4. 「まともなファイル」だけをリストアップ
    print("🔍 候補を選定中...", end="", flush=True)
    valid_candidates = []
    
    for filepath in files:
        # 行数チェック（軽めのフィルタリング）
        if get_line_count(filepath) >= MIN_ROWS:
            valid_candidates.append(filepath)
            
    print(f" 完了! (有効な候補: {len(valid_candidates)} 銘柄)")
    
    if not valid_candidates:
        print("❌ 有効なデータがありません。")
        return

    # 5. ★ここがポイント！ ランダムに100個選ぶ
    # 候補が100個未満なら全部、それ以上なら100個選ぶ
    select_count = min(len(valid_candidates), MAX_SAMPLES)
    target_files = random.sample(valid_candidates, select_count)
    
    print(f"🎯 ランダムに選ばれた {len(target_files)} 銘柄をコピーします...")
    print("-" * 60)
    
    count_copy = 0
    for filepath in target_files:
        filename = os.path.basename(filepath)
        
        # 5桁(xxxxx0) -> 4桁(xxxxx) コード変換
        raw_code = filename.replace("daily_", "").replace(".csv", "")
        if len(raw_code) == 5 and raw_code.endswith("0"):
            code_4 = raw_code[:-1]
        else:
            code_4 = raw_code
            
        new_filename = f"daily_{code_4}.csv"
        dst_path = os.path.join(DST_DIR, new_filename)
        
        try:
            shutil.copy2(filepath, dst_path)
            # print(f"   ✅ コピー: {code_4}")
            count_copy += 1
        except Exception as e:
            print(f"   ❌ コピー失敗: {e}")
            
    print("-" * 60)
    print("🎉 厳選引越し完了！")
    print(f"   data_raw フォルダに {count_copy} 銘柄の精鋭データが入りました。")
    print("\n👉 次のステップ（これで動きます！）:")
    print("   py -3.12 step4_process_daily_log.py")
    print("=" * 60)

if __name__ == "__main__":
    main()