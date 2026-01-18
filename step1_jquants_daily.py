# -*- coding: utf-8 -*-
"""
step1_jquants_daily.py v4.0 (Fixed)
===================================
J-Quants V2 API 全銘柄一括取得

修正点:
- 5桁コード(13010)を4桁(1301)に変換
- 調整後価格(AdjO, AdjC等)を使用
"""

import requests
import pandas as pd
import os
import time
from datetime import datetime

# ==========================================
# ★API Key (設定済み)
# ==========================================
MY_API_KEY = "REDACTED"

DATA_DIR = "data_archive_daily"
os.makedirs(DATA_DIR, exist_ok=True)
BASE_URL = "https://api.jquants.com/v2"

def get_headers():
    return {"x-api-key": MY_API_KEY}

def convert_code(code_5digit):
    """
    5桁コードを4桁に変換
    J-Quants: 13010 → 銘柄コード: 1301
    """
    code_str = str(code_5digit)
    if len(code_str) == 5 and code_str.endswith('0'):
        return code_str[:-1]
    return code_str

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
    if not os.path.exists(file_path): return None
    try:
        with open(file_path, 'rb') as f:
            try: f.seek(-200, os.SEEK_END)
            except OSError: f.seek(0)
            last_lines = f.readlines()
            if not last_lines: return None
            last_line = last_lines[-1].decode('utf-8', errors='ignore')
            return last_line.split(',')[0]
    except: return None

def main():
    print("=" * 60)
    print("🚀 J-Quants V2 日足データ更新 v4.0")
    print("=" * 60)
    
    now = datetime.now()
    target_date = now.strftime("%Y%m%d")
    
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
            # ★ 5桁コードを4桁に変換
            raw_code = str(row.get('Code', ''))
            code = convert_code(raw_code)
            
            # 4桁数字のみ対象
            if len(code) != 4 or not code.isdigit():
                skipped_count += 1
                continue
            
            file_path = os.path.join(DATA_DIR, f"daily_{code}.csv")
            
            # 日付文字列 (YYYY-MM-DD)
            date_raw = row.get('Date', '')
            if isinstance(date_raw, str) and len(date_raw) >= 10:
                date_str = date_raw[:10]
            else:
                date_str = str(date_raw).split(" ")[0]

            # 既存チェック
            if os.path.exists(file_path):
                last_date = get_last_date(file_path)
                if last_date == date_str:
                    continue # 既に最新
                mode = 'a'
                header = False
            else:
                mode = 'w'
                header = True
            
            # ★ 調整後価格を使用（株式分割等に対応）
            open_price = row.get('AdjO', row.get('O', 0))
            high_price = row.get('AdjH', row.get('H', 0))
            low_price = row.get('AdjL', row.get('L', 0))
            close_price = row.get('AdjC', row.get('C', 0))
            volume = row.get('AdjVo', row.get('Vo', 0))
            
            # 書き込み用DataFrame作成 (1行)
            line_data = pd.DataFrame([{
                'datetime': date_str,
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