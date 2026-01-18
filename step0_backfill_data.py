# -*- coding: utf-8 -*-
"""
step0_backfill_data.py v17.2 (Alpha-Num Fix)
============================================
【修正点】
銘柄コードの判定を `isdigit()` から `isalnum()` に変更しました。
これにより、"285A" などの英数字入り銘柄も正しく保存されます。

機能:
- すでに data フォルダにある銘柄はスキップ（高速化）
- まだ持っていない銘柄（285Aなど）だけをダウンロードして保存
"""

import requests
import pandas as pd
import os
import time
import glob
import shutil
from datetime import datetime, timedelta

# ==========================================
# API Key
# ==========================================
MY_API_KEY = "4tRpsBOq_cEf9yo6Db19OwS-3yeange8-saImd0mzCc"

DATA_DIR = "data"
BASE_URL = "https://api.jquants.com/v2"

YEARS_TO_FETCH = 3  # 3年分

def get_headers():
    return {"x-api-key": MY_API_KEY}

def convert_code(code_5digit):
    """5桁コードを4桁に変換"""
    code_str = str(code_5digit)
    if len(code_str) == 5 and code_str.endswith('0'):
        return code_str[:-1]
    return code_str

def get_all_quotes_for_date(target_date_str):
    """指定日の全銘柄データを取得"""
    url = f"{BASE_URL}/equities/bars/daily"
    params = {"date": target_date_str}
    all_data = []
    
    while True:
        try:
            res = requests.get(url, headers=get_headers(), params=params)
            if res.status_code != 200:
                return []
            data = res.json()
            
            quotes = []
            if "data" in data:
                quotes = data["data"]
            elif "daily_quotes" in data:
                quotes = data["daily_quotes"]
            elif "lists" in data:
                quotes = data["lists"]
            
            if quotes:
                all_data.extend(quotes)
            
            if "pagination_key" in data and data["pagination_key"]:
                params["pagination_key"] = data["pagination_key"]
                time.sleep(0.03)
            else:
                break
        except:
            break
            
    return all_data


def save_quotes(quotes, date_str, existing_codes):
    """データをファイルに保存"""
    saved = 0
    
    for row in quotes:
        try:
            # 5桁→4桁変換
            raw_code = str(row.get('Code', ''))
            code = convert_code(raw_code)
            
            # ★修正: 数字だけでなく英数字も許可 (285A対応)
            if len(code) != 4 or not code.isalnum():
                continue
            
            # 大文字に統一
            code = code.upper()

            # すでに持っている銘柄なら無視
            if code in existing_codes:
                continue
            
            file_path = os.path.join(DATA_DIR, f"merged_{code}.csv")
            
            # 調整後価格を使用
            open_price = row.get('AdjO', row.get('O', 0))
            high_price = row.get('AdjH', row.get('H', 0))
            low_price = row.get('AdjL', row.get('L', 0))
            close_price = row.get('AdjC', row.get('C', 0))
            volume = row.get('AdjVo', row.get('Vo', 0))
            
            if any(pd.isna(x) for x in [open_price, high_price, low_price, close_price, volume]):
                continue
            
            # 新規作成のみ
            if os.path.exists(file_path):
                mode = 'a'
                header = False
            else:
                mode = 'w'
                header = True
            
            line_data = pd.DataFrame([{
                'datetime': date_str,
                'open': open_price,
                'high': high_price,
                'low': low_price,
                'close': close_price,
                'volume': volume
            }])
            
            line_data.to_csv(file_path, mode=mode, header=header, index=False)
            saved += 1

        except:
            continue
    
    return saved


def main():
    print("=" * 60)
    print("📥 差分データ取得 v17.2 (英数字対応版)")
    print("=" * 60)
    
    os.makedirs(DATA_DIR, exist_ok=True)

    # 1. 既存銘柄の確認
    existing_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    existing_codes = set()
    
    for f in existing_files:
        code = os.path.basename(f).replace("merged_", "").replace(".csv", "").upper()
        existing_codes.add(code)
        
    print(f"🛡️ 既存データ: {len(existing_codes)} 銘柄 (これらはスキップします)")
    print("   ターゲット: 未取得の銘柄 (285Aなど) をダウンロードします")
    print("-" * 60)
    
    # 取得日数を計算
    total_days = YEARS_TO_FETCH * 365
    
    print(f"\n📡 データ取得開始 (過去 {YEARS_TO_FETCH} 年分)...")
    
    today = datetime.now()
    total_saved = 0
    fetch_count = 0
    
    start_date = today - timedelta(days=total_days)
    
    for i in range(total_days):
        target_date = start_date + timedelta(days=i)
        
        if target_date > today:
            break

        date_str = target_date.strftime("%Y-%m-%d")
        date_api = target_date.strftime("%Y%m%d")
        
        if target_date.weekday() >= 5:
            continue
        
        fetch_count += 1
        
        if fetch_count % 10 == 0:
            print(f"   [{fetch_count}/{total_days}] {date_str}...", end=" ")
        
        quotes = get_all_quotes_for_date(date_api)
        
        if quotes:
            saved = save_quotes(quotes, date_str, existing_codes)
            total_saved += saved
            if fetch_count % 10 == 0:
                print(f"✅ 新規保存: {saved}件")
        else:
            if fetch_count % 10 == 0:
                print("⏭️ データなし")
        
        time.sleep(0.05)
    
    print("\n" + "=" * 60)
    print(f"🎉 完了！")
    print(f"   これで 285A などの英字銘柄も data フォルダに入りました。")
    print("=" * 60)


if __name__ == "__main__":
    main()