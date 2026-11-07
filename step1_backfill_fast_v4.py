# -*- coding: utf-8 -*-
"""
step1_repair_data.py
====================
【目的】
提示された「ホワイトリスト銘柄」の過去データを強制的に取得し、
データ不足（1行しかない問題）を解決します。
"""

import os
import requests
import pandas as pd
import time
from datetime import datetime, timedelta

# ==========================================
# ★API Key (リフレッシュトークン)
# ==========================================
REFRESH_TOKEN = ""

DATA_DIR = "data_archive_daily"
BASE_URL = "https://api.jquants.com/v2"

# ★あなたが提示した「本当に欲しい銘柄」リスト
TARGET_CODES = [
    "7003", "6525", "9501", "6269", "6954", 
    "5016", "8306", "278A", "285A", "3110", 
    "7974", "9984", "6506", "2432", "2502", 
    "2127", "1332", "3382", "2201"
]

def get_id_token(refresh_token):
    """ログイン処理"""
    url = f"{BASE_URL}/token/auth_refresh"
    resp = requests.post(url, params={"refreshtoken": refresh_token})
    if resp.status_code == 200:
        return resp.json().get("idToken")
    print(f"❌ ログイン失敗: {resp.text}")
    return None

def main():
    print("=" * 60)
    print("🔧 指定銘柄 データ強制修復ツール")
    print(f"   対象: {len(TARGET_CODES)} 銘柄")
    print("=" * 60)
    
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # 1. ログイン
    print("🔑 J-Quantsにログイン中...", end=" ")
    id_token = get_id_token(REFRESH_TOKEN)
    if not id_token: return
    print("OK")
    
    headers = {"Authorization": f"Bearer {id_token}"}
    
    # 2. 期間設定 (過去2年)
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=730)
    str_from = from_dt.strftime("%Y%m%d")
    str_to = to_dt.strftime("%Y%m%d")

    success_count = 0

    for code in TARGET_CODES:
        print(f"📥 {code} 取得中...", end=" ")
        
        # J-Quantsのクセ: 4桁でダメなら5桁(末尾0)で試す
        try_codes = [code, code + "0"]
        all_quotes = []
        
        for api_code in try_codes:
            params = {
                "code": api_code,
                "from": str_from,
                "to": str_to,
                "pagination_key": ""
            }
            
            temp_quotes = []
            while True:
                try:
                    res = requests.get(f"{BASE_URL}/equities/bars/daily", headers=headers, params=params)
                    if res.status_code != 200: break
                    
                    data = res.json()
                    quotes = data.get("daily_quotes", [])
                    if not quotes: break
                    
                    temp_quotes.extend(quotes)
                    
                    if "pagination_key" in data and data["pagination_key"]:
                        params["pagination_key"] = data["pagination_key"]
                        time.sleep(0.1)
                    else:
                        break
                except: break
            
            # データが取れたら採用してループを抜ける
            if len(temp_quotes) > 0:
                all_quotes = temp_quotes
                break
        
        # 保存処理
        if len(all_quotes) > 30: # 30行以上あるかチェック
            # 整形
            output_data = []
            for q in all_quotes:
                output_data.append({
                    'datetime': q.get('Date', '').split(' ')[0],
                    'open': q.get('AdjustmentOpen') or q.get('Open'),
                    'high': q.get('AdjustmentHigh') or q.get('High'),
                    'low': q.get('AdjustmentLow') or q.get('Low'),
                    'close': q.get('AdjustmentClose') or q.get('Close'),
                    'volume': q.get('AdjustmentVolume') or q.get('Volume')
                })
            
            df = pd.DataFrame(output_data)
            df = df.sort_values('datetime')
            
            # 保存 (daily_XXXX.csv)
            save_path = os.path.join(DATA_DIR, f"daily_{code}.csv")
            df.to_csv(save_path, index=False)
            
            print(f"✅ OK ({len(df)}日分)")
            success_count += 1
        else:
            print(f"⚠️ データ不足 ({len(all_quotes)}行) - 上場直後かコード間違い")

    print("-" * 60)
    print(f"🎉 修復完了: {success_count} / {len(TARGET_CODES)} 銘柄")
    print("👉 これでもう一度 step4_process_daily_log.py を実行してください")

if __name__ == "__main__":
    main()