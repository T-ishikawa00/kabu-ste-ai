# -*- coding: utf-8 -*-
"""
fix_datetime_format.py
======================
既存CSVの日付形式を統一する修復スクリプト

問題:
  - 分足: "2026-01-19 06:24:00+00:00" (タイムゾーン付き)
  - 日足: "2026-01-20" (タイムゾーンなし)
  
解決:
  - "2026-01-20" → "2026-01-20 00:00:00+00:00" に変換
"""

import os
import re
import glob

DATA_DIR = "data_archive_daily"

def fix_file(filepath):
    """1ファイルの日付形式を修正"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        fixed = False
        new_lines = []
        
        # 日付のみの行を検出するパターン (YYYY-MM-DD,...)
        # タイムゾーン付きでない行をマッチ
        date_only_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2}),')
        
        for line in lines:
            match = date_only_pattern.match(line)
            if match:
                # "2026-01-20,..." → "2026-01-20 00:00:00+00:00,..."
                date_part = match.group(1)
                new_datetime = f"{date_part} 00:00:00+00:00"
                new_line = line.replace(date_part + ",", new_datetime + ",", 1)
                new_lines.append(new_line)
                fixed = True
            else:
                new_lines.append(line)
        
        if fixed:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            return True
        return False
        
    except Exception as e:
        print(f"  ❌ エラー: {e}")
        return False

def main():
    print("=" * 60)
    print("🔧 日付形式修復スクリプト")
    print("   'YYYY-MM-DD' → 'YYYY-MM-DD 00:00:00+00:00' に変換")
    print("=" * 60)
    
    files = glob.glob(os.path.join(DATA_DIR, "daily_*.csv"))
    
    if not files:
        print(f"❌ {DATA_DIR} にCSVファイルがありません")
        return
    
    print(f"\n📂 対象: {len(files)} ファイル")
    print("-" * 60)
    
    fixed_count = 0
    
    for filepath in files:
        code = os.path.basename(filepath).replace("daily_", "").replace(".csv", "")
        if fix_file(filepath):
            print(f"✅ {code}: 修正完了")
            fixed_count += 1
    
    print("-" * 60)
    print(f"🎉 完了！ {fixed_count} ファイルを修正しました")

if __name__ == "__main__":
    main()