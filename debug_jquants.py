import requests
import json

# ★ここにAPI Key
MY_API_KEY = ""

def test_connection():
    print("🔍 J-Quants 接続診断")
    print("-" * 40)

    # テスト対象: トヨタ (7203)
    # パターン1: 4桁コード (7203)
    # パターン2: 5桁コード (72030)
    # エンドポイント: V1 (現在使用中) と V2 (予想)
    
    headers = {"x-api-key": MY_API_KEY}
    
    # テスト1: 現在のURL (V1) + 4桁コード
    url_v1 = "https://api.jquants.com/v1/prices/prices_am_one_minute_quotes"
    params = {"code": "7203", "from": "20240101", "to": "20240105"}
    
    print(f"📡 1. URL: {url_v1}")
    print(f"   Code: 7203 (4桁)")
    try:
        res = requests.get(url_v1, headers=headers, params=params)
        print(f"   👉 Status: {res.status_code}") # 200ならOK, 401/403なら認証エラー
        print(f"   👉 Body: {res.text[:200]}")   # エラーメッセージのチラ見せ
    except Exception as e:
        print(f"   ❌ Error: {e}")

    print("-" * 40)

    # テスト2: 予想されるV2 URL (もしV1がダメな場合)
    # ※2025/12以降の新しいエンドポイントの可能性
    url_v2 = "https://api.jquants.com/v2/equities/bars/daily" # まずは日足でテスト
    params_v2 = {"code": "72030", "from": "20240101", "to": "20240105"} # V2は5桁かも？

    print(f"📡 2. URL: {url_v2} (日足テスト)")
    print(f"   Code: 72030 (5桁)")
    try:
        res = requests.get(url_v2, headers=headers, params=params_v2)
        print(f"   👉 Status: {res.status_code}")
        print(f"   👉 Body: {res.text[:200]}")
    except Exception as e:
        print(f"   ❌ Error: {e}")

if __name__ == "__main__":
    test_connection()