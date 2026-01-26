# -*- coding: utf-8 -*-
"""
step1_get_daily.py v17.2 (Date Format Fixed)
=============================================
J-Quants V2 API 全銘柄一括取得

【修正点 v17.2】
- 日付形式を分足データと統一 (YYYY-MM-DD HH:MM:SS+00:00)
- これにより step4 でのパースエラーを防止
"""

import requests
import pandas as pd
import os
import time
import re
from datetime import datetime

# ==========================================
# ★API Key
# ==========================================
MY_API_KEY = "REDACTED"

DATA_DIR = "data_archive_daily"
os.makedirs(DATA_DIR, exist_ok=True)
BASE_URL = "https://api.jquants.com/v2"

def get_headers():
    return {"x-api-key": MY_API_KEY}

def convert_code(code_raw):
    """
    コード正規化
    13010 -> 1301
    278A0 -> 278A
    """
    code_str = str(code_raw)
    if len(code_str) == 5 and code_str.endswith('0'):
        return code_str[:-1]
    return code_str

def is_valid_code(code_str):
    """
    有効な銘柄コードか判定 (数字または英字を含む4桁)
    例: "7203" -> True, "278A" -> True, "99840" -> False
    """
    if len(code_str) != 4:
        return False
    # 英数字のみ許可
    if not re.match(r'^[0-9A-Z]{4}$', code_str):
        return False
    return True

def normalize_datetime(date_raw):
    """
    日付を分足データと同じ形式に統一
    "2026-01-20" -> "2026-01-20 00:00:00+00:00"
    
    これにより step4 での pd.to_datetime() エラーを防止
    """
    if isinstance(date_raw, str) and len(date_raw) >= 10:
        date_str = date_raw[:10]  # "2026-01-20" 部分を取得
    else:
        date_str = str(date_raw).split(" ")[0]
    
    # 分足データと同じ形式に統一 (UTC表記)
    return f"{date_str} 00:00:00+00:00"

def get_all_quotes_for_date(target_date_str):
    print(f"📡 [J-Quants] {target_date_str} の全銘柄データを一括取得中...")
    url = f"{BASE_URL}/equities/bars/daily"
    params = {"date": target_date_str}
    all_data = []
    
    while True:
        try:
            res = requests.get(url, headers=get_headers(), params=params)
            if res.status_code != 200:
                print(f"❌ Error: {res.status_code} {res.text}")
                break
            data = res.json()
            
            quotes = []
            if "data" in data: quotes = data["data"]
            elif "lists" in data: quotes = data["lists"]
            elif isinstance(data, list): quotes = data
            
            if quotes: all_data.extend(quotes)
            
            if "pagination_key" in data and data["pagination_key"]:
                params["pagination_key"] = data["pagination_key"]
                time.sleep(0.05)
            else:
                break
        except Exception as e:
            print(f"❌ 通信エラー: {e}")
            break
            
    print(f"   ✅ 取得完了: {len(all_data)} 銘柄分")
    return all_data

def get_last_date(file_path):
    """最終行の日付部分だけを取得（形式に依存しない）"""
    if not os.path.exists(file_path): return None
    try:
        with open(file_path, 'rb') as f:
            try: f.seek(-300, os.SEEK_END)
            except OSError: f.seek(0)
            last_lines = f.readlines()
            if not last_lines: return None
            last_line = last_lines[-1].decode('utf-8', errors='ignore')
            datetime_part = last_line.split(',')[0]
            # "2026-01-20 00:00:00+00:00" -> "2026-01-20"
            # "2026-01-20" -> "2026-01-20"
            return datetime_part[:10]
    except: return None

def main():
    print("=" * 60)
    print("🚀 J-Quants V2 日足データ更新 v17.2 (日付形式統一版)")
    print("=" * 60)
    
    now = datetime.now()
    target_date = now.strftime("%Y%m%d")
    target_date_hyphen = now.strftime("%Y-%m-%d")  # 比較用
    
    # データ取得
    quotes = get_all_quotes_for_date(target_date)
    if not quotes:
        print("🍂 本日のデータはまだ配信されていないか、休場日です。")
        return

    print(f"\n💾 各ファイルに書き込み中 ({len(quotes)}件)...")
    
    updated_count = 0
    skipped_count = 0
    error_count = 0
    
    for i, row in enumerate(quotes):
        try:
            # 1. コード変換 (5桁 -> 4桁)
            raw_code = str(row.get('Code', ''))
            code = convert_code(raw_code)
            
            # 2. バリデーション (英数字4桁ならOK)
            if not is_valid_code(code):
                skipped_count += 1
                continue
            
            file_path = os.path.join(DATA_DIR, f"daily_{code}.csv")
            
            # ★修正: 日付文字列を分足と同じ形式に統一
            date_raw = row.get('Date', '')
            datetime_str = normalize_datetime(date_raw)
            date_only = datetime_str[:10]  # 比較用 "2026-01-20"

            # 既存チェック（日付部分だけで比較）
            if os.path.exists(file_path):
                last_date = get_last_date(file_path)
                if last_date == date_only:
                    continue # 既に最新
                mode = 'a'
                header = False
            else:
                mode = 'w'
                header = True
            
            # 調整後価格を使用
            open_price = row.get('AdjO', row.get('O', 0))
            high_price = row.get('AdjH', row.get('H', 0))
            low_price = row.get('AdjL', row.get('L', 0))
            close_price = row.get('AdjC', row.get('C', 0))
            volume = row.get('AdjVo', row.get('Vo', 0))
            
            line_data = pd.DataFrame([{
                'datetime': datetime_str,  # ★修正: 統一形式
                'open': open_price,
                'high': high_price,
                'low': low_price,
                'close': close_price,
                'volume': volume
            }])
            
            line_data.to_csv(file_path, mode=mode, header=header, index=False)
            updated_count += 1
            
            if updated_count % 500 == 0:
                print(f"   進捗: {updated_count} 更新済み", end="\r")

        except Exception as e:
            error_count += 1
            if error_count == 1:
                print(f"\n❌ 書き込みエラー発生 (例: {raw_code}): {e}")
            continue

    print(f"\n   ✅ 更新: {updated_count} ファイル")
    print(f"   ⏭️ スキップ: {skipped_count} (非4桁コード)")
    if error_count > 0:
        print(f"   ⚠️ エラー: {error_count} 件")

    print("\n" + "-" * 60)
    print(f"🎉 完了！")

if __name__ == "__main__":
    main()