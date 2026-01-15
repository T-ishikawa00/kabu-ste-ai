# -*- coding: utf-8 -*-
"""
step1_jquants_daily.py (J-Quants V2 Official Spec)
===================================================
J-Quants API V2 公式仕様準拠版
・認証: x-api-key ヘッダー
・銘柄一覧: /v2/equities/master
・株価: /v2/equities/bars/daily
・カラム: O, H, L, C, Vo, Va -> open, high...
"""

import requests
import pandas as pd
import os
import time
from datetime import datetime, timedelta

# ==========================================
# ★ここにあなたのAPI Keyを貼る
# (ダッシュボードから取得した x-api-key)
# ==========================================
MY_API_KEY = "REDACTED"

# 保存先
DATA_DIR = "data_archive_daily"
os.makedirs(DATA_DIR, exist_ok=True)

# 基準日（ファイルがない場合にここから取得）
DEFAULT_START_DATE = "20230101"

# API設定
BASE_URL = "https://api.jquants.com/v2"

def get_headers():
    """V2 APIキー認証用ヘッダー"""
    return {"x-api-key": MY_API_KEY}

def get_listed_info():
    """上場銘柄一覧を取得 (/v2/equities/master)"""
    print("📋 [V2] 上場銘柄一覧を取得中 (/v2/equities/master)...")
    url = f"{BASE_URL}/equities/master"
    
    # 過去の日付を指定するとその時点の銘柄が取れるが、
    # 今日現在の上場銘柄を知りたいので日付指定なし（または当日）でリクエスト
    params = {"date": datetime.now().strftime("%Y%m%d")}
    
    try:
        res = requests.get(url, headers=get_headers(), params=params)
        
        if res.status_code == 403:
            print("\n❌ 認証エラー (403): API Keyが間違っています。")
            return []
        
        if res.status_code != 200:
            print(f"⚠️ 銘柄一覧取得エラー: {res.status_code} {res.text}")
            return []

        data = res.json()
        
        # V2のレスポンス形式: {"lists": [...]} または {"data": [...]} の可能性
        # 公式ドキュメントではリストが直接返るケースや、特定のキーに入るケースがあるため柔軟に対応
        targets = []
        if "lists" in data:
            targets = data["lists"]
        elif "data" in data:
            targets = data["data"]
        elif isinstance(data, list):
            targets = data
            
        if targets:
             # V2は "Code" (大文字C)
             return [item['Code'] for item in targets if 'Code' in item]
             
        return []
    except Exception as e:
        print(f"❌ 銘柄一覧の取得失敗: {e}")
        return []

def get_daily_quotes(code, from_date, to_date):
    """
    日足データを取得 (/v2/equities/bars/daily)
    ページネーション対応
    """
    if from_date > to_date:
        return []

    url = f"{BASE_URL}/equities/bars/daily"
    all_quotes = []
    
    params = {
        "code": code,
        "from": from_date,
        "to": to_date
    }
    
    while True:
        try:
            res = requests.get(url, headers=get_headers(), params=params)
            
            if res.status_code == 429: # レートリミット対策
                time.sleep(2)
                continue
                
            if res.status_code != 200:
                break

            data = res.json()
            
            # レスポンスからデータリストを抽出
            quotes = []
            if "lists" in data:
                quotes = data["lists"]
            elif "data" in data:
                quotes = data["data"]
            elif isinstance(data, list):
                quotes = data
            
            if quotes:
                all_quotes.extend(quotes)
            
            # ページネーション (pagination_key)
            if "pagination_key" in data and data["pagination_key"]:
                params["pagination_key"] = data["pagination_key"]
                time.sleep(0.05)
            else:
                break
        except Exception:
            break
            
    return all_quotes

def get_last_date(file_path):
    """CSVの最後の日付を取得する"""
    if not os.path.exists(file_path):
        return None
    try:
        if os.path.getsize(file_path) < 50:
            return None
        df = pd.read_csv(file_path)
        if df.empty or 'datetime' not in df.columns:
            return None
        return df['datetime'].max()
    except:
        return None

def main():
    print("=" * 60)
    print("🚀 J-Quants V2 日足データ取得 (API Key方式)")
    print("=" * 60)
    
    # 1. 銘柄一覧
    codes = get_listed_info()
    if not codes:
        print("❌ 銘柄一覧が取得できませんでした。処理を終了します。")
        return

    print(f"🎯 対象: {len(codes)} 銘柄")
    
    today_str = datetime.now().strftime("%Y%m%d")
    updated_count = 0
    uptodate_count = 0
    
    print("\n🍵 ダウンロード開始...\n")
    
    for i, code in enumerate(codes):
        file_path = os.path.join(DATA_DIR, f"daily_{code}.csv")
        last_date_str = get_last_date(file_path)
        
        if last_date_str:
            try:
                last_dt = pd.to_datetime(last_date_str)
                start_date = (last_dt + timedelta(days=1)).strftime("%Y%m%d")
                mode = "差分"
            except:
                start_date = DEFAULT_START_DATE
                mode = "修復"
        else:
            start_date = DEFAULT_START_DATE
            mode = "新規"
            
        if start_date > today_str:
            uptodate_count += 1
            if i % 100 == 0: print(f"⏩ [{i+1}/{len(codes)}] {code} 最新済", end="\r")
            continue

        print(f"[{i+1}/{len(codes)}] {code} ({mode}) ...", end="", flush=True)
        
        quotes = get_daily_quotes(code, start_date, today_str)
        
        if quotes:
            df = pd.DataFrame(quotes)
            
            # ★ V2のカラム名 (O, H, L, C, Vo, Va) を bot仕様 (open, high...) に変換
            rename_map = {
                "Date": "datetime", 
                "O": "open", 
                "H": "high", 
                "L": "low", 
                "C": "close", 
                "Vo": "volume", 
                "Va": "trading_value"
            }
            df.rename(columns=rename_map, inplace=True)
            
            if 'datetime' in df.columns:
                df['datetime'] = pd.to_datetime(df['datetime'])
                df.sort_values('datetime', inplace=True)
                
                # 保存するカラム
                cols = ["datetime", "open", "high", "low", "close", "volume"]
                cols = [c for c in cols if c in df.columns]
                df = df[cols]
                
                if mode == "差分" and os.path.exists(file_path):
                    df.to_csv(file_path, mode='a', header=False, index=False)
                else:
                    df.to_csv(file_path, mode='w', header=True, index=False)
                    
                print(f" ✅ +{len(df)}日")
                updated_count += 1
            else:
                print(" ⚠️ データ形式エラー (カラム名不一致)")
                print(f"    取得カラム: {df.columns.tolist()}")
        else:
            print(" ー なし")
            
        time.sleep(0.02)

    print("\n" + "-" * 60)
    print(f"🎉 完了！")
    print(f"   更新: {updated_count} / 最新済: {uptodate_count}")

if __name__ == "__main__":
    main()