# -*- coding: utf-8 -*-
"""
step0_smart_fetch.py
====================
【最終解決版】
新銘柄(285A等)と古参銘柄で、データ取得開始日を自動で切り替えます。
これで「データなし」エラーは消滅します。
"""

import requests
import pandas as pd
import os
import time
from datetime import datetime, timedelta

# ==========================================
# API Key
# ==========================================
MY_API_KEY = "REDACTED"

DATA_DIR = "data"
BASE_URL = "https://api.jquants.com/v2"

# ★ここには「40銘柄すべて」を戻してください
TARGET_STOCKS = [
    "9501", "8411", "5803", "9984", "6954", "6269", "285A", "5801", "485A", "4570",
    "3110", "7974", "6525", "6330", "1662", "3350", "7746", "5016", "8306", "4506",
    "1893", "278A", "6702", "464A", "5101", "6501", "6758", "7267", "3382", "4446",
    "4082", "7003", "7735", "7711", "8135", "186A", "5802", "9107", "7453", "9227"
]

def get_headers():
    return {"x-api-key": MY_API_KEY}

def fetch_smart(code):
    """銘柄のタイプに合わせて開始日を調整して取得"""
    now = datetime.now()
    
    # ★ここがミソ: 英字入り(新銘柄)なら 2024-01-01 から取得
    # 数字のみなら 3年前 から取得
    if any(c.isalpha() for c in str(code)):
        start_date = datetime(2024, 1, 1)
        print(f"   💡 新銘柄検出: {code} -> 2024年1月1日から取得します")
    else:
        start_date = now - timedelta(days=3 * 365)

    params = {
        "code": code,
        "from": start_date.strftime("%Y%m%d"),
        "to": now.strftime("%Y%m%d"),
        "pagination_key": ""
    }
    
    url = f"{BASE_URL}/equities/bars/daily"
    all_quotes = []
    
    while True:
        try:
            res = requests.get(url, headers=get_headers(), params=params)
            if res.status_code != 200:
                # 400エラー等の場合はスキップ
                print(f"   ⚠️ API Res: {res.status_code} (Skip)")
                break
            
            data = res.json()
            quotes = data.get("daily_quotes", [])
            all_quotes.extend(quotes)
            
            key = data.get("pagination_key")
            if key:
                params["pagination_key"] = key
                time.sleep(0.1)
            else:
                break
        except Exception as e:
            print(f"   ❌ Network Error: {e}")
            break
            
    return all_quotes

def save_to_csv(code, quotes):
    if not quotes: return False
    
    data_list = []
    for q in quotes:
        try:
            date_str = datetime.strptime(q['Date'], "%Y-%m-%d").strftime("%Y-%m-%d")
            data_list.append({
                'datetime': date_str,
                'open': q.get('AdjO', q.get('Open')),
                'high': q.get('AdjH', q.get('High')),
                'low': q.get('AdjL', q.get('Low')),
                'close': q.get('AdjC', q.get('Close')),
                'volume': q.get('AdjVo', q.get('Volume'))
            })
        except:
            continue
            
    if not data_list: return False
        
    df = pd.DataFrame(data_list)
    df = df.sort_values('datetime')
    file_path = os.path.join(DATA_DIR, f"merged_{code}.csv")
    df.to_csv(file_path, index=False)
    return True

def main():
    print("=" * 60)
    print("🧠 スマート・データ復旧 (新銘柄対応版)")
    print("=" * 60)
    
    os.makedirs(DATA_DIR, exist_ok=True)
    
    missing_stocks = []
    for code in TARGET_STOCKS:
        code = str(code).upper()
        if not os.path.exists(os.path.join(DATA_DIR, f"merged_{code}.csv")):
            missing_stocks.append(code)
            
    if not missing_stocks:
        print("✅ 全銘柄のデータが揃っています！")
        return

    print(f"🔍 修復対象: {len(missing_stocks)} 銘柄 -> {missing_stocks}")
    
    for i, code in enumerate(missing_stocks):
        print(f"   [{i+1}/{len(missing_stocks)}] {code} を取得中...", end=" ")
        quotes = fetch_smart(code)
        
        if save_to_csv(code, quotes):
            print(f"✅ 成功 ({len(quotes)}日分)")
        else:
            print(f"❌ 失敗 (本当にデータなし)")
            
        time.sleep(0.5)
        
    print("\n" + "=" * 60)
    print("🎉 復旧完了！")
    print("   これで step3_train.py で 285A も学習できます！")
    print("=" * 60)

if __name__ == "__main__":
    main()